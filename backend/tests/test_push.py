"""Web Push 回归：VAPID 公钥 / 订阅管理 / 提醒点判定 / 送达去重 / 失效订阅清理。

所有用例绝不触网：发送一律 monkeypatch `app.push.send_to_subscription`；
调度循环在 lifespan 里 60s 才跑一轮，测试生命周期内不会自发推送。
"""
from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

from app.infra import push as push_module
from app.infra.db import SessionLocal
from app.infra.models import PushDelivery, PushSubscription, Task
from app.infra.timeutil import utcnow

ENDPOINT = "https://push.example.com/abc123"
KEYS = {"p256dh": "BPx" * 40, "auth": "auth-secret"}


def _subscribe(client, endpoint=ENDPOINT):
    return client.post("/api/push/subscribe", json={
        "endpoint": endpoint,
        "keys": KEYS,
        "expiration_time": None,
    })


def _unsubscribe(client, endpoint=ENDPOINT):
    return client.request("DELETE", "/api/push/subscribe", json={"endpoint": endpoint})


def _make_task(due_in_hours: float, offsets=None, done=False) -> Task:
    with SessionLocal() as db:
        t = Task(
            title=f"任务{due_in_hours}",
            due_at=utcnow() + timedelta(hours=due_in_hours),
            remind_offsets_h=offsets if offsets is not None else [72, 24, 2],
            done=done,
        )
        db.add(t)
        db.commit()
        db.refresh(t)
        return t


# ---------------------------------------------------------------------------
# 状态与订阅管理
# ---------------------------------------------------------------------------


def test_info_returns_vapid_key_and_count(client):
    r = client.get("/api/push/info")
    assert r.status_code == 200
    data = r.json()
    assert data["supported"] is True  # pywebpush 已装
    assert len(data["vapid_public_key"]) >= 80  # 65 字节原始公钥的 base64url
    assert data["subscriptions"] == 0
    # 密钥已持久化，重启（再次加载）仍是同一对
    with SessionLocal():
        pass
    assert push_module.get_vapid_keys()["public_raw_b64url"] == data["vapid_public_key"]
    assert push_module.settings.vapid_keys_file.exists()


def test_subscribe_and_unsubscribe_roundtrip(client):
    assert _subscribe(client).status_code == 200
    assert client.get("/api/push/info").json()["subscriptions"] == 1
    # 同端点重复订阅 = 更新，不产生新行
    assert _subscribe(client).status_code == 200
    assert client.get("/api/push/info").json()["subscriptions"] == 1
    assert _unsubscribe(client).status_code == 200
    assert client.get("/api/push/info").json()["subscriptions"] == 0
    assert _unsubscribe(client).status_code == 404


def test_vapid_private_key_format_parseable_by_pyvapid(client):
    """回归：py_vapid 要求 base64url 的 PKCS8 DER 私钥（传 PEM 字符串会在发送时报
    「Could not deserialize key data」），并验证私钥与下发公钥同一密钥对。"""
    import base64

    from cryptography.hazmat.primitives import serialization
    from py_vapid import Vapid

    keys = push_module.get_vapid_keys()
    Vapid.from_string(keys["private_der_b64url"])  # 格式错误会在这里抛 ValueError
    der = base64.urlsafe_b64decode(keys["private_der_b64url"] + "==")
    priv = serialization.load_der_private_key(der, password=None)
    raw = priv.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    pub_b64url = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    assert pub_b64url == keys["public_raw_b64url"]


def test_subscribe_rejects_bad_endpoint(client):
    r = client.post("/api/push/subscribe", json={
        "endpoint": "http://insecure.example.com/x", "keys": KEYS,
    })
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# 提醒点判定（纯计算）
# ---------------------------------------------------------------------------


def test_baseline_silences_history_on_subscribe(client):
    """订阅时已过期的提醒点（含逾期任务）一律静默标记——不轰炸历史。"""
    _make_task(due_in_hours=-5)   # 逾期
    _make_task(due_in_hours=20)   # 72h/24h 点都已过期
    _make_task(due_in_hours=200)  # 全部未到
    assert _subscribe(client).status_code == 200
    with SessionLocal() as db:
        assert push_module.pending_deliveries(db) == []


def test_pending_delivery_uses_most_recent_crossed_offset(client):
    """任务在 72h 点之后才创建：只按距截止最近的已跨过点补发一条，而非三条轰炸。"""
    assert _subscribe(client).status_code == 200
    _make_task(due_in_hours=3)  # 72h/24h 已跨过，2h 未到 → 只发「24h」这一条
    with SessionLocal() as db:
        pend = push_module.pending_deliveries(db)
        assert len(pend) == 1
        sub, task, offset_h = pend[0]
        assert offset_h == 24
        assert task.title == "任务3"


def test_done_tasks_never_remind(client):
    assert _subscribe(client).status_code == 200
    _make_task(due_in_hours=1, done=True)
    with SessionLocal() as db:
        assert push_module.pending_deliveries(db) == []


def test_format_notification_local_time_and_course():
    task = _make_task(due_in_hours=-1)
    payload = push_module.format_notification(task, 2, utcnow(), "操作系统")
    assert payload["title"] == "StudyOS · DDL 提醒"
    assert "已到截止时间" in payload["body"]
    assert "操作系统" in payload["body"]
    assert payload["tag"] == f"task-{task.id}"
    # 未逾期时按提醒点措辞：72 小时 → 「3 天」
    task2 = _make_task(due_in_hours=71)
    payload2 = push_module.format_notification(task2, 72, utcnow(), None)
    assert "3 天" in payload2["body"] and "已到截止时间" not in payload2["body"]


# ---------------------------------------------------------------------------
# 调度发送（monkeypatch 发送函数，绝不触网）
# ---------------------------------------------------------------------------


class _FakeSender:
    def __init__(self, monkeypatch):
        self.calls = []
        monkeypatch.setattr(push_module, "send_to_subscription", self)

    def __call__(self, sub, payload):
        self.calls.append((sub.endpoint, payload))


def test_tick_sends_once_then_stops(client, monkeypatch):
    assert _subscribe(client).status_code == 200
    _make_task(due_in_hours=-0.2)  # 刚逾期：三个提醒点都已跨过 → 补发一条
    sender = _FakeSender(monkeypatch)
    stats = push_module.push_tick()
    assert stats["sent"] == 1 and stats["removed"] == 0 and stats["skipped"] == 0
    assert len(sender.calls) == 1
    endpoint, payload = sender.calls[0]
    assert endpoint == ENDPOINT
    assert "已到截止时间" in payload["body"]
    # 再跑一轮：送达标记在，不重发
    assert push_module.push_tick()["sent"] == 0
    assert len(sender.calls) == 1
    with SessionLocal() as db:
        assert db.query(PushDelivery).count() == 1


def test_tick_removes_gone_subscription(client, monkeypatch):
    """推送服务 404/410（浏览器已取消订阅）→ 删掉失效订阅，不留脏数据。"""
    assert _subscribe(client).status_code == 200
    _make_task(due_in_hours=-1)

    def fake_send(sub, payload):
        raise push_module.WebPushException(
            "gone", response=SimpleNamespace(status_code=410)
        )

    monkeypatch.setattr(push_module, "send_to_subscription", fake_send)
    stats = push_module.push_tick()
    assert stats["removed"] == 1 and stats["sent"] == 0
    with SessionLocal() as db:
        assert db.query(PushSubscription).count() == 0
    assert client.get("/api/push/info").json()["subscriptions"] == 0


def test_tick_skips_transient_failure_and_retries(client, monkeypatch):
    """临时故障（如 500）：不写送达标记，下轮重试——提醒不丢。"""
    assert _subscribe(client).status_code == 200
    _make_task(due_in_hours=-1)

    def flaky_send(sub, payload):
        raise push_module.WebPushException(
            "boom", response=SimpleNamespace(status_code=500)
        )

    monkeypatch.setattr(push_module, "send_to_subscription", flaky_send)
    assert push_module.push_tick()["skipped"] == 1
    with SessionLocal() as db:
        assert db.query(PushDelivery).count() == 0  # 未标记 → 下轮重试


def test_test_notification_route(client, monkeypatch):
    assert _subscribe(client).status_code == 200
    sender = _FakeSender(monkeypatch)
    r = client.post("/api/push/test")
    assert r.status_code == 200
    assert r.json()["sent"] == 1
    assert "Web Push 链路已打通" in sender.calls[0][1]["body"]


def test_unsubscribe_clears_delivery_markers(client, monkeypatch):
    assert _subscribe(client).status_code == 200
    _make_task(due_in_hours=-1)
    _FakeSender(monkeypatch)
    assert push_module.push_tick()["sent"] == 1
    with SessionLocal() as db:
        assert db.query(PushDelivery).count() == 1
    assert _unsubscribe(client).status_code == 200
    with SessionLocal() as db:
        assert db.query(PushDelivery).count() == 0
        assert db.query(PushSubscription).count() == 0
