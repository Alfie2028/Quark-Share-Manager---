"""数据模型定义 — SQLAlchemy + SQLite"""

from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Boolean, ForeignKey
from sqlalchemy.orm import declarative_base, sessionmaker
import enum

Base = declarative_base()


class UserLevel(str, enum.Enum):
    ADMIN = "admin"       # 管理员
    VIP = "vip"           # VIP（试题卷免费，课设另购）
    NORMAL = "normal"     # 普通用户（全部需购买）


class OrderStatus(str, enum.Enum):
    PENDING = "pending"         # 待支付
    CONFIRMING = "confirming"   # 用户已点"我已支付"，等待管理员确认
    PAID = "paid"               # 管理员确认到账
    CANCELLED = "cancelled"     # 已取消


class Material(Base):
    """资料表"""
    __tablename__ = "materials"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False, comment="资料名称")
    category = Column(String(100), nullable=False, default="未分类", comment="分类标签")
    quark_folder_id = Column(String(100), nullable=False, comment="夸克网盘文件夹ID")
    price = Column(Float, nullable=False, default=0.0, comment="单价（元），0=免费")
    is_active = Column(Boolean, default=True, comment="是否上架")
    created_at = Column(DateTime, default=datetime.utcnow)


class User(Base):
    """用户表"""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(50), unique=True, nullable=False)
    password_hash = Column(String(200), nullable=False)
    level = Column(String(20), nullable=False, default=UserLevel.NORMAL.value, comment="admin/vip/normal")
    wechat_id = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Order(Base):
    """订单表"""
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    material_id = Column(Integer, ForeignKey("materials.id"), nullable=False)
    price = Column(Float, nullable=False, comment="购买时单价")
    status = Column(String(20), nullable=False, default=OrderStatus.PENDING.value, comment="pending/paid/cancelled")
    created_at = Column(DateTime, default=datetime.utcnow)


class ShareLog(Base):
    """分享记录表"""
    __tablename__ = "share_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    material_id = Column(Integer, ForeignKey("materials.id"), nullable=False)
    share_url = Column(String(500), nullable=False, comment="夸克分享链接")
    password = Column(String(20), nullable=True, comment="提取码")
    created_at = Column(DateTime, default=datetime.utcnow)


# ── 数据库初始化 ──

import os

DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
os.makedirs(DB_DIR, exist_ok=True)
DB_PATH = os.path.join(DB_DIR, "quark_share.db")

engine = create_engine(f"sqlite:///{DB_PATH}", echo=False)
SessionLocal = sessionmaker(bind=engine)


def init_db():
    """创建所有表"""
    Base.metadata.create_all(engine)


def get_db():
    """获取数据库会话"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
