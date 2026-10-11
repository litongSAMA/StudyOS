"""真正的 Web Push：Service Worker push 订阅 + 后端调度推送（页面关闭也能收到）。

与旧实现（页面开着的 `new Notification()`）的本质区别：
1. 浏览器把 `PushSubscription`（endpoint + p256dh/auth）交给后端；
2. 后端用 VAPID 私钥经浏览器厂商推送服务（FCM/APNs/Mozilla）把消息推到
   **Service Worker 的 `push` 事件**——浏览器进程收到推送后唤醒 SW 弹系统通知，
   不需要打开 StudyOS 页面；
3. 调度器每分钟检查一次任务：某个「提前 N 小时」提醒点被跨过且未发过 → 推送一次。
   送达记录落 `push_deliveries` 表，重启不重发；新订阅创建时把已过期的提醒点
   静默标记为已送达（基线），避免一开推送就收到历史轰炸。

VAPID 密钥对持久化在 data/vapid_keys.json（重启后订阅仍然有效）；
更换/丢失会作废所有旧订阅（浏览器推送服务会拒绝 audience 不匹配的消息）。

设备本地概念：订阅端点绑定「浏览器 × 本机」，不参与多端同步快照。
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import threading
from datetime import timedelta
from urllib.parse import urlparse

from sqlalchemy import func, select, text

from .config import settings
from .db import SessionLocal
from .models import PushDelivery, PushSubscription, Task
from .serializers import course_names
from .timeutil import local_datetime, utcnow

logger = logging.getLogger("studyos.push")

# ---------------------------------------------------------------------------
# 推送库（可选依赖：未安装时订阅照常存储，发送降级为「未支持」提示）
# ---------------------------------------------------------------------------

try:  # pragma: no cover - 安装情况随环境而定
    from pywebpush import WebPushException, webpush

    PUSH_LIB_AVAILABLE = True
except Exception:  # pragma: no cover
    WebPushException = Exception  # type: ignore[assignment,misc]
    webpush = None
    PUSH_LIB_AVAILABLE = False

DEFAULT_OFFSETS = [72, 24, 2]
PUSH_TTL = 6 * 3600
_tick_lock = threading.Lock()
_health = {"last_tick_at":None,"last_tick":None,"last_error":None,"consecutive_failures":0,"degraded":False}

# ---------------------------------------------------------------------------
# VAPID 密钥对
# ---------------------------------------------------------------------------

_vapid_cache: dict | None = None


def _generate_vapid() -> dict:
    """生成 P-256 密钥对。

    - `private_der_b64url`：PKCS8 DER 的 base64url（pywebpush/py_vapid 要求的格式）；
    - `private_pem`：同私钥 PEM（留档 / 备用）；
    - `public_raw_b64url`：65 字节未压缩公钥的 base64url（前端 applicationServerKey 用）。
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")
    private_der = key.private_bytes(
        serialization.Encoding.DER,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_raw = key.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    return {
        "private_der_b64url": base64.urlsafe_b64encode(private_der).rstrip(b"=").decode("ascii"),
        "private_pem": private_pem,
        "public_raw_b64url": base64.urlsafe_b64encode(public_raw).rstrip(b"=").decode("ascii"),
    }


def _pem_to_der_b64url(pem: str) -> str:
    from cryptography.hazmat.primitives import serialization

    key = serialization.load_pem_private_key(pem.encode("ascii"), password=None)
    der = key.private_bytes(
        serialization.Encoding.DER,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return base64.urlsafe_b64encode(der).rstrip(b"=").decode("ascii")


def get_vapid_keys() -> dict:
    """加载（缺失则生成并持久化）VAPID 密钥对。"""
    global _vapid_cache
    if _vapid_cache is not None:
        return _vapid_cache
    path = settings.vapid_keys_file
    keys: dict | None = None
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if loaded.get("private_pem") and loaded.get("public_raw_b64url"):
                keys = loaded
                if not keys.get("private_der_b64url"):  # 兼容只有 PEM 的旧文件
                    keys["private_der_b64url"] = _pem_to_der_b64url(keys["private_pem"])
        except (OSError, ValueError):
            keys = None
    if keys is None:
        keys = _generate_vapid()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(keys, indent=2), encoding="utf-8")
            tmp.replace(path)
        except OSError:  # 目录不可写也先跑着：重启后订阅会失效，属可接受降级
            logger.exception("无法持久化 VAPID 密钥，重启后旧订阅将失效")
    _vapid_cache = keys
    return keys


# ---------------------------------------------------------------------------
# 订阅管理
# ---------------------------------------------------------------------------


def _offsets_of(task: Task) -> list[int]:
    raw = task.remind_offsets_h if task.remind_offsets_h is not None else DEFAULT_OFFSETS
    return sorted({int(o) for o in raw if int(o) > 0})


def _delivered_keys(db, subscription_id: int) -> set[tuple[int, int]]:
    rows = db.execute(
        select(PushDelivery.task_id, PushDelivery.offset_h).where(
            PushDelivery.subscription_id == subscription_id
        )
    ).all()
    return {(r.task_id, r.offset_h) for r in rows}


def _mark_crossed_baseline(db, sub: PushSubscription, now=None) -> None:
    """新订阅静默基线：已过期的提醒点全部标记为已送达（不轰炸历史任务）。"""
    now = now or utcnow()
    marked = _delivered_keys(db, sub.id)
    for t in db.execute(select(Task).where(Task.done == False)).scalars().all():
        for o in _offsets_of(t):
            if t.due_at - timedelta(hours=o) <= now and (t.id, o) not in marked:
                db.add(PushDelivery(task_id=t.id, subscription_id=sub.id, offset_h=o))


def store_subscription(
    db, endpoint: str, p256dh: str, auth: str,
    user_agent: str = "", expires_at=None,
) -> PushSubscription:
    """保存/更新订阅。新订阅把已跨过的提醒点记为已送达（静默基线）。"""
    sub = db.execute(
        select(PushSubscription).where(PushSubscription.endpoint == endpoint)
    ).scalars().first()
    if sub is None:
        sub = PushSubscription(
            endpoint=endpoint,
            p256dh=p256dh,
            auth=auth,
            user_agent=(user_agent or "")[:300],
            expires_at=expires_at,
        )
        db.add(sub)
        db.flush()  # 拿到 id 才能写基线
        _mark_crossed_baseline(db, sub)
    else:
        sub.p256dh = p256dh
        sub.auth = auth
        sub.user_agent = (user_agent or "")[:300]
        sub.expires_at = expires_at
    db.commit()
    db.refresh(sub)
    return sub


def remove_subscription(db, endpoint: str) -> bool:
    sub = db.execute(
        select(PushSubscription).where(PushSubscription.endpoint == endpoint)
    ).scalars().first()
    if sub is None:
        return False
    db.execute(
        PushDelivery.__table__.delete().where(PushDelivery.subscription_id == sub.id)
    )
    db.delete(sub)
    db.commit()
    return True


def subscription_count(db) -> int:
    return int(db.execute(select(func.count(PushSubscription.id))).scalar_one())


# ---------------------------------------------------------------------------
# 提醒判定与消息
# ---------------------------------------------------------------------------


def pending_deliveries(db, now=None) -> list[tuple[PushSubscription, Task, int]]:
    """待推送列表（纯计算，不发送不落库）：每条 = (订阅, 任务, 刚跨过的提醒点)。

    语义：取**最近跨过**的提醒点——检查点时刻 = 截止时间 - offset，offset 越小越晚，
    所以 `crossed[0]`（最小 offset）就是距现在最近的那个提醒点。任务在「提前 72 小时」
    之后才创建时，72h/24h 点都已过期，只按最近的点（如 24h）补发一条，而不是三条轰炸。
    """
    now = now or utcnow()
    subs = list(db.execute(select(PushSubscription)).scalars().all())
    if not subs:
        return []
    out: list[tuple[PushSubscription, Task, int]] = []
    tasks = list(db.execute(select(Task).where(Task.done == False)).scalars().all())
    for sub in subs:
        marked = _delivered_keys(db, sub.id)
        for t in tasks:
            crossed = [o for o in _offsets_of(t) if t.due_at - timedelta(hours=o) <= now]
            if not crossed:
                continue
            recent = crossed[0]  # 距截止最近的提醒点（offset 升序，最小 = 最晚跨过）
            if (t.id, recent) not in marked:
                out.append((sub, t, recent))
    return out


def format_notification(task: Task, offset_h: int, now=None, course_name: str | None = None) -> dict:
    """推送消息负载（SW `push` 事件里 showNotification 用）。时间按设备本地时区展示。"""
    now = now or utcnow()
    due = local_datetime(task.due_at)
    due_str = due.strftime("%m月%d日 %H:%M")
    if task.due_at <= now:
        lead = "已到截止时间"
    elif offset_h >= 24 and offset_h % 24 == 0:
        lead = f"距截止不足 {offset_h // 24} 天"
    else:
        lead = f"距截止不足 {offset_h} 小时"
    course = f" · {course_name}" if course_name else ""
    return {
        "title": "StudyOS · DDL 提醒",
        "body": f"{lead}：{task.title}{course}（{due_str} 截止）",
        "tag": f"task-{task.id}",
        "url": "/tasks",
        "task_id": task.id,
        "due_at": task.due_at.isoformat()+"Z",
    }


def send_to_subscription(sub: PushSubscription, payload: dict) -> None:
    """经浏览器推送服务投递（WebPush 协议，VAPID 签名）。失败抛异常由调用方处理。"""
    if not PUSH_LIB_AVAILABLE:
        raise RuntimeError("pywebpush 未安装")
    webpush(
        {
            "endpoint": sub.endpoint,
            "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
        },
        json.dumps(payload, ensure_ascii=False),
        # py_vapid 要求 base64url 编码的 PKCS8 DER 私钥（PEM 字符串会解析失败）
        ttl=PUSH_TTL,
        vapid_private_key=get_vapid_keys()["private_der_b64url"],
        vapid_claims={"sub": settings.vapid_subject},
        timeout=15,
    )


# ---------------------------------------------------------------------------
# 调度
# ---------------------------------------------------------------------------


def push_tick() -> dict:
    """Persist acceptance only against the same deadline; retry transport failures."""
    if not PUSH_LIB_AVAILABLE:
        return {"sent":0,"removed":0,"skipped":0,"lib_available":False}
    if not _tick_lock.acquire(blocking=False):
        return {"sent":0,"removed":0,"skipped":0,"lib_available":True,"busy":True}
    sent=removed=skipped=0
    last_error=None
    try:
        with SessionLocal() as db:
            now=utcnow();names=course_names(db)
            candidates=[(sub.id,task.id,task.due_at,offset) for sub,task,offset in pending_deliveries(db,now)]
        for sid,tid,due,offset in candidates:
            with SessionLocal() as db:
                sub=db.get(PushSubscription,sid);task=db.get(Task,tid)
                if not sub or not task or task.done or task.due_at!=due or (tid,offset) in _delivered_keys(db,sid):
                    skipped+=1;continue
                payload=format_notification(task,offset,now,names.get(task.course_id))
                # Detached immutable delivery snapshot: no write transaction spans the network.
                db.expunge(sub)
            try:
                send_to_subscription(sub,payload)
            except Exception as error:
                response=getattr(error,'response',None)
                status=response.status_code if response is not None else None
                if status in (404,410):
                    with SessionLocal() as db:
                        db.query(PushDelivery).filter_by(subscription_id=sid).delete()
                        db.query(PushSubscription).filter_by(id=sid).delete();db.commit()
                    removed+=1
                else:
                    skipped+=1;last_error=f'推送暂时失败 ({status or "网络/配置"})，下轮重试'
                continue
            with SessionLocal() as db:
                db.execute(text('BEGIN IMMEDIATE'))
                current=db.get(Task,tid)
                if current and not current.done and current.due_at==due and db.get(PushSubscription,sid) and (tid,offset) not in _delivered_keys(db,sid):
                    db.add(PushDelivery(task_id=tid,subscription_id=sid,offset_h=offset))
                    sent+=1
                else:
                    skipped+=1
                db.commit()
        result={"sent":sent,"removed":removed,"skipped":skipped,"lib_available":True}
        failures=0 if sent else _health['consecutive_failures']+1 if last_error else _health['consecutive_failures']
        _health.update(last_tick_at=utcnow().isoformat()+'Z',last_tick=result,last_error=last_error,consecutive_failures=failures,degraded=failures>=2)
        return result
    finally:
        _tick_lock.release()


_scheduler_tasks: dict = {}  # 每个事件循环一个调度任务（测试里多个 TestClient 各有各的 loop）


async def _scheduler_loop() -> None:
    interval = max(5, int(settings.push_tick_seconds))
    while True:
        try:
            await asyncio.sleep(interval)
            await asyncio.to_thread(push_tick)
        except asyncio.CancelledError:
            raise
        except Exception:  # 循环绝不死：单轮异常只记日志
            logger.exception("push 调度轮次异常")


def start_push_scheduler() -> None:
    """lifespan 里启动（按事件循环幂等）。"""
    loop = asyncio.get_running_loop()
    task = _scheduler_tasks.get(loop)
    if task is None or task.done():
        _scheduler_tasks[loop] = loop.create_task(_scheduler_loop())


async def stop_push_scheduler() -> None:
    """lifespan 退出时取消**当前事件循环**的调度任务。"""
    task = _scheduler_tasks.pop(asyncio.get_running_loop(), None)
    if task is None or task.done():
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


# ---------------------------------------------------------------------------
# 状态 / 测试通知
# ---------------------------------------------------------------------------


def _mask_endpoint(endpoint: str) -> str:
    try:
        return urlparse(endpoint).netloc or endpoint[:40]
    except ValueError:
        return endpoint[:40]


def push_info(db) -> dict:
    return {
        "supported": PUSH_LIB_AVAILABLE,
        "vapid_public_key": get_vapid_keys()["public_raw_b64url"],
        "subscriptions": subscription_count(db),
        "tick_seconds": int(settings.push_tick_seconds),
        "ttl_seconds": PUSH_TTL,
        "health": dict(_health),
    }


def send_test_notification(db) -> dict:
    """设置页「发送测试通知」：给所有订阅各发一条，逐订阅回报结果。"""
    results = []
    if not PUSH_LIB_AVAILABLE:
        return {"sent": 0, "results": results, "lib_available": False}
    payload = {
        "title": "StudyOS · 测试推送",
        "body": "Web Push 链路已打通——现在关掉本页面也能收到 DDL 提醒。",
        "tag": "studyos-push-test",
        "url": "/tasks",
    }
    for sub in db.execute(select(PushSubscription)).scalars().all():
        try:
            send_to_subscription(sub, payload)
            results.append({"endpoint": _mask_endpoint(sub.endpoint), "ok": True})
        except WebPushException as e:  # type: ignore[misc]
            status = e.response.status_code if getattr(e, "response", None) is not None else None
            if status in (404, 410):
                db.delete(sub)
                error = "推送服务返回 410/404，订阅已失效"
            elif status is not None:
                error = f"推送服务返回 {status}，稍后重试"
            else:
                error = "无法连接推送服务（网络错误），稍后重试"
            results.append({
                "endpoint": _mask_endpoint(sub.endpoint),
                "ok": False,
                "error": error,
            })
        except Exception as e:
            results.append({
                "endpoint": _mask_endpoint(sub.endpoint),
                "ok": False,
                "error": str(e)[:160],
            })
    db.commit()
    return {
        "sent": sum(1 for r in results if r["ok"]),
        "results": results,
        "lib_available": True,
    }
