"""Web Push 路由：VAPID 公钥 / 订阅管理 / 状态 / 测试通知。"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import get_db
from ..push import (
    push_info,
    remove_subscription,
    send_test_notification,
    store_subscription,
    subscription_count,
)

router = APIRouter(prefix="/api/push", tags=["push"])


class PushKeys(BaseModel):
    p256dh: str
    auth: str


class PushSubscribeIn(BaseModel):
    endpoint: str
    keys: PushKeys
    # PushSubscriptionJSON.expirationTime：epoch 毫秒或 null
    expiration_time: float | None = None


class PushUnsubscribeIn(BaseModel):
    endpoint: str


def _expiry(epoch_ms: float | None) -> datetime | None:
    if not epoch_ms or epoch_ms <= 0:
        return None
    try:
        return datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc).replace(tzinfo=None)
    except (OverflowError, OSError, ValueError):
        return None


@router.get("/info")
def info(db: Session = Depends(get_db)):
    return push_info(db)


@router.post("/subscribe")
def subscribe(body: PushSubscribeIn, request: Request, db: Session = Depends(get_db)):
    """浏览器把 PushSubscription 交给后端；已跨过的提醒点静默记为已送达。"""
    endpoint = body.endpoint.strip()
    if not endpoint.startswith("https://"):
        raise HTTPException(422, "无效的推送订阅端点")
    store_subscription(
        db,
        endpoint,
        body.keys.p256dh,
        body.keys.auth,
        user_agent=request.headers.get("user-agent", ""),
        expires_at=_expiry(body.expiration_time),
    )
    return {"ok": True, "subscriptions": subscription_count(db)}


@router.delete("/subscribe")
def unsubscribe(body: PushUnsubscribeIn, db: Session = Depends(get_db)):
    removed = remove_subscription(db, body.endpoint.strip())
    if not removed:
        raise HTTPException(404, "该订阅不存在")
    return {"ok": True, "subscriptions": subscription_count(db)}


@router.post("/test")
def test_notification(db: Session = Depends(get_db)):
    """给所有订阅发一条测试通知（设置页验证链路用）。"""
    return send_test_notification(db)
