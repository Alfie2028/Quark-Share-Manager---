"""PushPlus 微信通知"""

import urllib.request
import json

PUSHPLUS_TOKEN = ""


def send_order_notify(username: str, items: list, total: float):
    """有新订单时发送微信通知给管理员"""
    if not PUSHPLUS_TOKEN:
        return  # 未配置，静默跳过

    items_text = "\n".join([f"  • {item['material_name']} — ¥{item['price']:.2f}" for item in items])
    content = f"""📦 新订单通知

用户：{username}
资料：{len(items)} 件，合计 ¥{total:.2f}

{items_text}

请登录后台确认支付 → <a href="http://localhost:8000">夸克资料库后台</a>"""

    try:
        data = json.dumps({
            "token": PUSHPLUS_TOKEN,
            "title": f"📦 新订单 — {username} 购买 {len(items)} 件 ¥{total:.2f}",
            "content": content,
            "template": "html",
        }).encode()
        req = urllib.request.Request(
            "http://www.pushplus.plus/send",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass  # 通知失败不影响下单
