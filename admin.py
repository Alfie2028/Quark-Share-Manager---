"""管理端 API — 资料管理、用户管理、订单管理、分享记录"""

import os
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel

from models import get_db, Material, User, ShareLog, Order, OrderStatus
from user import get_current_user

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ── 请求模型 ──

class AdminLoginRequest(BaseModel):
    username: str
    password: str

class MaterialCreate(BaseModel):
    name: str
    category: str = "未分类"
    quark_folder_id: str
    price: float = 0.0

class MaterialUpdate(BaseModel):
    name: str = None
    category: str = None
    quark_folder_id: str = None
    price: float = None
    is_active: bool = None

class UserUpdate(BaseModel):
    level: str = None

class BatchImportRequest(BaseModel):
    folders: list[dict]  # [{name, quark_folder_id, category}]


# ── 分类默认价格 ──

CATEGORY_DEFAULT_PRICE = {
    "期末复习资料库": 1.0,
    "课程设计": 9.0,
}

def get_category_price(category: str) -> float:
    return CATEGORY_DEFAULT_PRICE.get(category, 0.0)


# ── 管理员认证 ──

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "admin123"


def verify_admin(token: str):
    import base64
    try:
        decoded = base64.b64decode(token).decode()
        username, password = decoded.split(":", 1)
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            return True
    except Exception:
        pass
    return False


def admin_required(token: str):
    if not verify_admin(token):
        raise HTTPException(status_code=403, detail="管理员权限不足")
    return token


# ── 管理员登录 ──

@router.post("/login")
def admin_login(req: AdminLoginRequest):
    import base64
    if req.username == ADMIN_USERNAME and req.password == ADMIN_PASSWORD:
        token = base64.b64encode(f"{req.username}:{req.password}".encode()).decode()
        return {"token": token}
    raise HTTPException(status_code=400, detail="管理员用户名或密码错误")


# ── 资料管理 ──

@router.post("/materials")
def create_material(data: MaterialCreate, token: str, db: Session = Depends(get_db)):
    admin_required(token)
    # 校验文件夹ID是否存在
    from quark_api import QuarkClient
    qc = getattr(_user_module, 'QUARK_COOKIE', '')
    if qc:
        try:
            quark = QuarkClient(qc)
            quark.list_folder(data.quark_folder_id)
        except Exception:
            raise HTTPException(status_code=400, detail=f"夸克文件夹ID无效或无法访问: {data.quark_folder_id}")

    # 价格为 0 时自动使用分类默认价
    price = data.price if data.price > 0 else get_category_price(data.category)
    material = Material(
        name=data.name,
        category=data.category,
        quark_folder_id=data.quark_folder_id,
        price=price,
    )
    db.add(material)
    db.commit()
    db.refresh(material)
    return {"id": material.id, "name": material.name, "price": material.price}


@router.put("/materials/{material_id}")
def update_material(material_id: int, data: MaterialUpdate, token: str, db: Session = Depends(get_db)):
    admin_required(token)
    material = db.query(Material).filter(Material.id == material_id).first()
    if not material:
        raise HTTPException(status_code=404, detail="资料不存在")
    if data.name is not None:
        material.name = data.name
    if data.category is not None:
        material.category = data.category
    if data.quark_folder_id is not None:
        material.quark_folder_id = data.quark_folder_id
    if data.price is not None:
        material.price = data.price
    if data.is_active is not None:
        material.is_active = data.is_active
    db.commit()
    return {"ok": True}


@router.delete("/materials/{material_id}")
def delete_material(material_id: int, token: str, db: Session = Depends(get_db)):
    """软删除：下架资料而非物理删除，保留订单和分享记录"""
    admin_required(token)
    material = db.query(Material).filter(Material.id == material_id).first()
    if not material:
        raise HTTPException(status_code=404, detail="资料不存在")
    material.is_active = False
    db.commit()
    return {"ok": True, "message": f"「{material.name}」已下架"}


@router.get("/materials")
def list_all_materials(token: str, db: Session = Depends(get_db)):
    admin_required(token)
    materials = db.query(Material).order_by(Material.category, Material.name).all()
    categories = {}
    for m in materials:
        cat = m.category or "未分类"
        if cat not in categories:
            categories[cat] = []
        categories[cat].append({
            "id": m.id,
            "name": m.name,
            "category": m.category,
            "quark_folder_id": m.quark_folder_id,
            "price": m.price,
            "is_active": m.is_active,
            "created_at": str(m.created_at),
        })
    return {"categories": categories}


# ── 批量导入 ──

import user as _user_module
import json as _json
from threading import Lock

SCAN_CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "scan_cache.json")
_scan_lock = Lock()

def _save_scan_cache(data: dict):
    with _scan_lock:
        with open(SCAN_CACHE_PATH, "w", encoding="utf-8") as f:
            _json.dump(data, f, ensure_ascii=False, indent=2)

def _load_scan_cache() -> dict:
    if os.path.exists(SCAN_CACHE_PATH):
        with open(SCAN_CACHE_PATH, "r", encoding="utf-8") as f:
            return _json.load(f)
    return {"folders": [], "total": 0, "imported_count": 0, "updated_at": ""}

@router.get("/quark-scan-cache")
def get_scan_cache():
    """获取缓存的扫描结果（无需管理员权限，所有用户可查看）"""
    return _load_scan_cache()

@router.get("/quark-scan")
def scan_quark_folders(token: str, keyword: str = "农大", db: Session = Depends(get_db)):
    """扫描夸克网盘：一级关键词过滤 → 扫描二级子文件夹（最小售卖单元）→ 提取分类名 → 更新缓存"""
    admin_required(token)
    from quark_api import QuarkClient
    from datetime import datetime, timezone, timedelta
    quark_cookie = getattr(_user_module, 'QUARK_COOKIE', '')
    if not quark_cookie:
        raise HTTPException(status_code=400, detail="未配置夸克Cookie")

    quark = QuarkClient(quark_cookie)

    # 1. 扫描根目录，找一级文件夹（含关键词的）
    try:
        root_items = quark.list_folder("0")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"扫描夸克网盘根目录失败: {e}")

    l1_folders = [i for i in root_items if i.get("is_dir")]
    if keyword:
        l1_folders = [f for f in l1_folders if keyword in f.get("name", "")]

    imported_ids = {m.quark_folder_id for m in db.query(Material).all()}

    categories = []
    total_items = 0
    total_imported = 0

    # 2. 遍历每个一级文件夹，扫描其下的二级文件夹
    for l1 in l1_folders:
        l1_name = l1.get("name", "")
        l1_fid = l1.get("file_id", "")

        # 提取分类名：最后一个"-"之后的内容
        dash_idx = l1_name.rfind('-')
        cat_name = l1_name[dash_idx + 1:].strip() if dash_idx >= 0 else l1_name

        # 扫描二级目录
        try:
            l2_items = quark.list_folder(l1_fid)
        except Exception:
            l2_items = []

        l2_folders = [i for i in l2_items if i.get("is_dir")]

        items = []
        cat_imported = 0
        for l2 in l2_folders:
            fid = l2["file_id"]
            name = l2["name"]
            is_imported = fid in imported_ids
            mat = db.query(Material).filter(Material.quark_folder_id == fid).first()
            items.append({
                "name": name,
                "quark_folder_id": fid,
                "imported": is_imported,
                "existing": {"id": mat.id, "name": mat.name, "category": mat.category, "price": mat.price} if mat else None,
            })
            if is_imported:
                cat_imported += 1

        categories.append({
            "category_name": cat_name,
            "l1_name": l1_name,
            "l1_folder_id": l1_fid,
            "items": items,
            "total": len(items),
            "imported_count": cat_imported,
        })
        total_items += len(items)
        total_imported += cat_imported

    data = {
        "categories": categories,
        "total": total_items,
        "imported_count": total_imported,
        "updated_at": datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M"),
    }
    _save_scan_cache(data)
    return data


@router.post("/materials/batch-import")
def batch_import_materials(req: BatchImportRequest, token: str, db: Session = Depends(get_db)):
    """批量导入选中的文件夹为资料"""
    admin_required(token)
    imported_ids = {m.quark_folder_id for m in db.query(Material).all()}
    created = []
    skipped = []

    for f in req.folders:
        fid = f.get("quark_folder_id", "")
        name = f.get("name", "").strip()
        if not fid or not name:
            continue
        if fid in imported_ids:
            skipped.append(name)
            continue
        cat = f.get("category", "未分类")
        price = get_category_price(cat)
        material = Material(name=name, quark_folder_id=fid, category=cat, price=price)
        db.add(material)
        db.flush()
        created.append({"id": material.id, "name": name, "quark_folder_id": fid})
        imported_ids.add(fid)

    db.commit()
    return {"created": created, "skipped": skipped, "message": f"导入 {len(created)} 个，跳过 {len(skipped)} 个"}


# ── 用户管理 ──

@router.get("/users")
def list_users(token: str, db: Session = Depends(get_db)):
    admin_required(token)
    users = db.query(User).order_by(User.created_at.desc()).all()
    level_names = {"admin": "管理员", "vip": "VIP", "normal": "普通"}
    return {
        "users": [
            {
                "id": u.id,
                "username": u.username,
                "level": u.level,
                "level_name": level_names.get(u.level, u.level),
                "wechat_id": u.wechat_id,
                "created_at": str(u.created_at),
            }
            for u in users
        ]
    }


@router.put("/users/{user_id}")
def update_user(user_id: int, data: UserUpdate, token: str, db: Session = Depends(get_db)):
    admin_required(token)
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    if data.level is not None:
        if data.level not in ("admin", "vip", "normal"):
            raise HTTPException(status_code=400, detail="无效级别，可用: admin/vip/normal")
        user.level = data.level
    db.commit()
    return {"ok": True}


# ── 订单管理 ──

@router.get("/orders")
def list_orders(token: str, status: str = None, page: int = 1, page_size: int = 20, date_from: str = None, date_to: str = None, db: Session = Depends(get_db)):
    """订单列表，可按状态/日期筛选，分页"""
    admin_required(token)
    from datetime import datetime
    query = db.query(Order)
    if status:
        query = query.filter(Order.status == status)
    if date_from:
        try:
            dt = datetime.strptime(date_from, "%Y-%m-%d")
            query = query.filter(Order.created_at >= dt)
        except ValueError:
            pass
    if date_to:
        try:
            dt = datetime.strptime(date_to, "%Y-%m-%d")
            dt = dt.replace(hour=23, minute=59, second=59)
            query = query.filter(Order.created_at <= dt)
        except ValueError:
            pass
    total = query.count()
    total_pages = max(1, (total + page_size - 1) // page_size)
    orders = query.order_by(Order.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()

    # 获取用户名和资料名映射
    user_ids = {o.user_id for o in orders}
    material_ids = {o.material_id for o in orders}
    users_map = {u.id: u.username for u in db.query(User).filter(User.id.in_(user_ids)).all()}
    mats_map = {m.id: m.name for m in db.query(Material).filter(Material.id.in_(material_ids)).all()}

    status_names = {"pending": "待支付", "confirming": "等待确认", "paid": "已支付", "cancelled": "已取消"}

    # 已支付订单查对应的分享码
    paid_orders = [o for o in orders if o.status == OrderStatus.PAID.value]
    share_map = {}
    if paid_orders:
        from models import ShareLog
        for o in paid_orders:
            sl = db.query(ShareLog).filter(
                ShareLog.user_id == o.user_id,
                ShareLog.material_id == o.material_id,
            ).order_by(ShareLog.created_at.desc()).first()
            if sl:
                share_map[o.id] = {"share_url": sl.share_url, "password": sl.password}

    return {
        "orders": [
            {
                "id": o.id,
                "user_id": o.user_id,
                "username": users_map.get(o.user_id, "?"),
                "material_id": o.material_id,
                "material_name": mats_map.get(o.material_id, "?"),
                "price": o.price,
                "status": o.status,
                "status_name": status_names.get(o.status, o.status),
                "created_at": str(o.created_at),
                "share": share_map.get(o.id),
            }
            for o in orders
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
    }


@router.put("/orders/{order_id}/pay")
def confirm_payment(order_id: int, token: str, db: Session = Depends(get_db)):
    """管理员确认交易成功"""
    admin_required(token)
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="订单不存在")
    if order.status not in (OrderStatus.PENDING.value, OrderStatus.CONFIRMING.value):
        raise HTTPException(status_code=400, detail="当前订单状态不允许此操作")
    order.status = OrderStatus.PAID.value
    db.commit()
    return {"ok": True, "status": "paid", "message": "交易成功，用户可获取分享码"}


@router.put("/orders/{order_id}/cancel")
def cancel_order(order_id: int, token: str, db: Session = Depends(get_db)):
    """取消订单"""
    admin_required(token)
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="订单不存在")
    order.status = OrderStatus.CANCELLED.value
    db.commit()
    return {"ok": True, "status": "cancelled"}


# ── 分享记录 ──

@router.get("/share-logs")
def list_share_logs(token: str, db: Session = Depends(get_db)):
    admin_required(token)
    logs = (
        db.query(ShareLog)
        .order_by(ShareLog.created_at.desc())
        .limit(500)
        .all()
    )
    # 批量取用户名和资料名（避免 N+1）
    user_ids = {l.user_id for l in logs}
    mat_ids = {l.material_id for l in logs}
    user_map = {u.id: u.username for u in db.query(User).filter(User.id.in_(user_ids)).all()}
    mat_map = {m.id: m.name for m in db.query(Material).filter(Material.id.in_(mat_ids)).all()}

    return {
        "logs": [
            {
                "id": log.id,
                "user_id": log.user_id,
                "username": user_map.get(log.user_id, "已删除"),
                "material_id": log.material_id,
                "material_name": mat_map.get(log.material_id, "已下架"),
                "share_url": log.share_url,
                "password": log.password,
                "created_at": str(log.created_at),
            }
            for log in logs
        ]
    }
