"""用户端 API — 注册、登录、浏览资料、下单、获取分享码"""

from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
import bcrypt as _bcrypt
from jose import jwt, JWTError

from models import get_db, User, Material, ShareLog, Order, UserLevel, OrderStatus
from quark_api import QuarkClient
from notify import send_order_notify

router = APIRouter(prefix="/api", tags=["user"])

JWT_SECRET = "change-me-to-a-random-string"
JWT_ALGORITHM = "HS256"

# 由 main.py 启动时设置
QUARK_COOKIE = ""
SHARE_EXPIRE_DAYS = 1
SHARE_VISITOR_LIMIT = 1

# VIP 免费分类
VIP_FREE_CATEGORY = "期末复习资料库"


# ── 请求/响应模型 ──

class RegisterRequest(BaseModel):
    username: str
    password: str

class LoginRequest(BaseModel):
    username: str
    password: str

class RequestShareRequest(BaseModel):
    material_ids: list[int]

class CreateOrderRequest(BaseModel):
    material_ids: list[int]


# ── JWT 工具 ──

def create_token(user_id: int, username: str, level: str = "normal") -> str:
    payload = {
        "user_id": user_id,
        "username": username,
        "level": level,
        "exp": datetime.utcnow() + timedelta(days=7),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def get_current_user(token: str, db: Session) -> User:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        user_id = payload.get("user_id")
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=401, detail="用户不存在")
        return user
    except JWTError:
        raise HTTPException(status_code=401, detail="无效的登录凭证")


# ── 注册/登录 ──

@router.post("/register")
def register(req: RegisterRequest, db: Session = Depends(get_db)):
    existing = db.query(User).filter(User.username == req.username).first()
    if existing:
        raise HTTPException(status_code=400, detail="用户名已存在")
    user = User(
        username=req.username,
        password_hash=_bcrypt.hashpw(req.password.encode(), _bcrypt.gensalt()).decode(),
        level=UserLevel.NORMAL.value,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    token = create_token(user.id, user.username, user.level)
    return {"token": token, "user_id": user.id, "username": user.username, "level": user.level}


@router.post("/login")
def login(req: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == req.username).first()
    if not user or not _bcrypt.checkpw(req.password.encode(), user.password_hash.encode()):
        raise HTTPException(status_code=400, detail="用户名或密码错误")
    token = create_token(user.id, user.username, user.level)
    return {"token": token, "user_id": user.id, "username": user.username, "level": user.level}


# ── 资料浏览 ──

@router.get("/materials")
def list_materials(db: Session = Depends(get_db)):
    """获取所有上架资料，按分类组织，含价格"""
    materials = (
        db.query(Material)
        .filter(Material.is_active == True)
        .order_by(Material.category, Material.name)
        .all()
    )
    categories = {}
    for m in materials:
        cat = m.category or "未分类"
        if cat not in categories:
            categories[cat] = []
        categories[cat].append({
            "id": m.id,
            "name": m.name,
            "price": m.price,
        })
    return {"categories": categories}


# ── 权限判断 ──

def _get_accessible_materials(user: User, db: Session) -> set:
    """返回用户可直接获取分享码的资料ID集合（无需支付）"""
    if user.level == UserLevel.ADMIN.value:
        all_ids = db.query(Material.id).filter(Material.is_active == True).all()
        return {row[0] for row in all_ids}

    if user.level == UserLevel.VIP.value:
        # VIP: 期末复习资料库免费
        free_ids = (
            db.query(Material.id)
            .filter(Material.is_active == True, Material.category == VIP_FREE_CATEGORY)
            .all()
        )
        return {row[0] for row in free_ids}

    # 普通用户：无免费资料
    return set()


def _get_paid_materials(user: User, db: Session) -> set:
    """返回用户已支付可获取分享码的资料ID集合"""
    paid = (
        db.query(Order.material_id)
        .filter(Order.user_id == user.id, Order.status == OrderStatus.PAID.value)
        .all()
    )
    return {row[0] for row in paid}


# ── 下单 ──

@router.post("/orders")
def create_orders(req: CreateOrderRequest, token: str, db: Session = Depends(get_db)):
    """为选中的资料创建订单"""
    user = get_current_user(token, db)

    # 检查哪些资料需要购买（免费的不需要下单）
    free_ids = _get_accessible_materials(user, db)
    need_buy = [mid for mid in req.material_ids if mid not in free_ids]

    if not need_buy:
        return {"orders": [], "message": "所选资料全部免费，可直接获取分享码"}

    created = []
    total = 0.0
    for mid in need_buy:
        material = db.query(Material).filter(Material.id == mid, Material.is_active == True).first()
        if not material:
            continue

        # 检查是否已有未支付或已支付的订单
        existing = (
            db.query(Order)
            .filter(
                Order.user_id == user.id,
                Order.material_id == mid,
                Order.status.in_([OrderStatus.PENDING.value, OrderStatus.PAID.value]),
            )
            .first()
        )
        if existing:
            continue  # 已有有效订单，跳过

        order = Order(
            user_id=user.id,
            material_id=mid,
            price=material.price,
            status=OrderStatus.PENDING.value,
        )
        db.add(order)
        db.flush()  # 获取 order.id
        created.append({"id": order.id, "material_id": mid, "material_name": material.name, "price": material.price})
        total += material.price

    db.commit()

    # 微信通知管理员
    if created:
        send_order_notify(user.username, created, total)

    return {
        "orders": created,
        "total": total,
        "message": f"已创建 {len(created)} 个订单，合计 ¥{total}，请联系管理员支付",
    }


@router.put("/orders/{order_id}/confirm")
def confirm_order(order_id: int, token: str, db: Session = Depends(get_db)):
    """用户点击「我已支付」"""
    user = get_current_user(token, db)
    order = db.query(Order).filter(Order.id == order_id, Order.user_id == user.id).first()
    if not order:
        raise HTTPException(status_code=404, detail="订单不存在")
    if order.status != OrderStatus.PENDING.value:
        raise HTTPException(status_code=400, detail="订单状态不允许此操作")
    order.status = OrderStatus.CONFIRMING.value
    db.commit()
    return {"ok": True, "status": "confirming", "message": "已提交确认，等待管理员核对到账"}


@router.get("/orders")
def my_orders(token: str, db: Session = Depends(get_db)):
    """查看我的订单"""
    user = get_current_user(token, db)
    orders = (
        db.query(Order)
        .filter(Order.user_id == user.id)
        .order_by(Order.created_at.desc())
        .limit(100)
        .all()
    )
    return {
        "orders": [
            {
                "id": o.id,
                "material_id": o.material_id,
                "price": o.price,
                "status": o.status,
                "created_at": str(o.created_at),
            }
            for o in orders
        ]
    }


# ── 获取分享码 ──

@router.post("/request-share")
def request_share(
    req: RequestShareRequest,
    token: str,
    db: Session = Depends(get_db),
):
    """请求资料分享码"""
    user = get_current_user(token, db)

    # 免费资料
    free_ids = _get_accessible_materials(user, db)
    # 已支付资料
    paid_ids = _get_paid_materials(user, db)

    # 可获取的全部资料
    allowed = free_ids | paid_ids

    denied = [mid for mid in req.material_ids if mid not in allowed]
    if denied:
        denied_names = (
            db.query(Material.name).filter(Material.id.in_(denied)).all()
        )
        names = ", ".join([n[0] for n in denied_names])
        # 区分原因
        need_pay = [mid for mid in denied if mid not in free_ids]
        if need_pay:
            raise HTTPException(
                status_code=403,
                detail=f"以下资料需购买后才能获取: {names}",
            )
        raise HTTPException(
            status_code=403,
            detail=f"以下资料不在你的权限范围内: {names}",
        )

    # 生成分享码
    try:
        quark = QuarkClient(QUARK_COOKIE)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"夸克连接失败: {e}")

    expire_label = "永久有效" if SHARE_EXPIRE_DAYS == 0 else f"{SHARE_EXPIRE_DAYS}天"
    results = []
    for mid in req.material_ids:
        material = db.query(Material).filter(Material.id == mid).first()
        if not material:
            continue

        # 已支付用户：如果已有分享码，直接复用不重新生成
        if mid in paid_ids:
            existing_share = (
                db.query(ShareLog)
                .filter(ShareLog.user_id == user.id, ShareLog.material_id == mid)
                .order_by(ShareLog.created_at.desc())
                .first()
            )
            if existing_share:
                results.append({
                    "material_id": mid,
                    "material_name": material.name,
                    "share_url": existing_share.share_url,
                    "password": existing_share.password,
                    "expire": expire_label,
                })
                continue

        try:
            share = quark.create_share(
                file_ids=[material.quark_folder_id],
                title=material.name,
                expire_days=SHARE_EXPIRE_DAYS,
                visitor_limit=SHARE_VISITOR_LIMIT,
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"生成「{material.name}」分享码失败: {e}")

        log = ShareLog(
            user_id=user.id,
            material_id=mid,
            share_url=share["share_url"],
            password=share["password"],
        )
        db.add(log)

        results.append({
            "material_id": mid,
            "material_name": material.name,
            "share_url": share["share_url"],
            "password": share["password"],
            "expire": expire_label,
        })

    db.commit()
    return {"shares": results}
