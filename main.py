"""夸克网盘分享码管理系统 — FastAPI 入口"""

import os
import yaml
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from contextlib import asynccontextmanager

from models import init_db


# ── 加载配置 ──

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.yaml")

def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

config = load_config()

# 注入配置到各模块
import user as user_module
import admin as admin_module

user_module.JWT_SECRET = config.get("jwt_secret", "change-me")
user_module.QUARK_COOKIE = config.get("quark_cookie", "")
user_module.SHARE_EXPIRE_DAYS = config.get("share", {}).get("expire_days", 1)
user_module.SHARE_VISITOR_LIMIT = config.get("share", {}).get("visitor_limit", 1)
admin_module.ADMIN_USERNAME = config.get("admin", {}).get("username", "admin")
admin_module.ADMIN_PASSWORD = config.get("admin", {}).get("password", "admin123")

import notify as notify_module
notify_module.PUSHPLUS_TOKEN = config.get("pushplus_token", "")


# ── 种子数据 ──

def seed_data():
    """初始化管理员和VIP用户"""
    from models import SessionLocal, User
    import bcrypt
    db = SessionLocal()
    try:
        # 管理员
        admin_user = db.query(User).filter(User.username == "admin").first()
        if not admin_user:
            db.add(User(username="admin", password_hash=bcrypt.hashpw(b"admin123", bcrypt.gensalt()).decode(), level="admin"))
        # VIP
        vip_user = db.query(User).filter(User.username == "vip").first()
        if not vip_user:
            db.add(User(username="vip", password_hash=bcrypt.hashpw(b"test", bcrypt.gensalt()).decode(), level="vip"))
        db.commit()
    finally:
        db.close()


# ── 应用初始化 ──

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    seed_data()
    yield

app = FastAPI(title="夸克分享码管理", lifespan=lifespan)

static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
app.mount("/static", StaticFiles(directory=static_dir), name="static")

from user import router as user_router
from admin import router as admin_router

app.include_router(user_router)
app.include_router(admin_router)


@app.get("/", response_class=HTMLResponse)
def index():
    html_path = os.path.join(static_dir, "index.html")
    with open(html_path, "r", encoding="utf-8") as f:
        return f.read()


@app.get("/api/quark-cookie-status")
def cookie_status():
    return {"configured": bool(user_module.QUARK_COOKIE)}


@app.get("/api/share-config")
def share_config():
    return {
        "expire_days": user_module.SHARE_EXPIRE_DAYS,
        "visitor_limit": user_module.SHARE_VISITOR_LIMIT,
    }


if __name__ == "__main__":
    import uvicorn
    host = config.get("server", {}).get("host", "0.0.0.0")
    port = config.get("server", {}).get("port", 8000)
    print(f"夸克分享码管理系统: http://{host}:{port}")
    uvicorn.run(app, host=host, port=port)
