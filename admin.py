"""管理端 API — 资料管理、用户管理、订单管理、分享记录"""

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
    material = Material(
        name=data.name,
        category=data.category,
        quark_folder_id=data.quark_folder_id,
        price=data.price,
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
    admin_required(token)
    material = db.query(Material).filter(Material.id == material_id).first()
    if not material:
        raise HTTPException(status_code=404, detail="资料不存在")
    db.delete(material)
    db.commit()
    return {"ok": True}


@router.get("/materials")
def list_all_materials(token: str, db: Session = Depends(get_db)):
    admin_required(token)
    materials = db.query(Material).order_by(Material.category, Material.name).all()
    return {
        "materials": [
            {
                "id": m.id,
                "name": m.name,
                "category": m.category,
                "quark_folder_id": m.quark_folder_id,
                "price": m.price,
                "is_active": m.is_active,
                "created_at": str(m.created_at),
            }
            for m in materials
        ]
    }


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
def list_orders(token: str, status: str = None, db: Session = Depends(get_db)):
    """订单列表，可按状态筛选 pending/paid/cancelled"""
    admin_required(token)
    query = db.query(Order)
    if status:
        query = query.filter(Order.status == status)
    orders = query.order_by(Order.created_at.desc()).limit(200).all()

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
        ]
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
    return {
        "logs": [
            {
                "id": log.id,
                "user_id": log.user_id,
                "material_id": log.material_id,
                "share_url": log.share_url,
                "password": log.password,
                "created_at": str(log.created_at),
            }
            for log in logs
        ]
    }
