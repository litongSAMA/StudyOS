"""健康检查 / 统计 / 系统设置。"""
from datetime import datetime, timedelta


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["app"] == "StudyOS"
    # 测试环境强制离线规则引擎 + 关闭 OCR
    assert body["llm"] == "offline(rule engine)"
    assert body["ocr"] == "unavailable"


def test_stats_counts(client):
    # 空库
    s = client.get("/api/stats").json()
    assert s["materials"] == 0
    assert s["courses"] == 0
    assert s["tasks_open"] == 0
    assert s["tasks_due_7d"] == 0
    assert s["tasks_overdue"] == 0
    assert s["upcoming"] == []

    c = client.post("/api/courses", json={"name": "操作系统"}).json()
    client.post("/api/materials", json={
        "course_id": c["id"], "title": "课件", "type": "courseware",
        "text": "进程调度与死锁", "tags": ["死锁"],
    })
    client.post("/api/tasks", json={
        "course_id": c["id"], "title": "2天后作业",
        "due_at": (datetime.now() + timedelta(days=2)).isoformat(),
    })
    client.post("/api/tasks", json={
        "course_id": c["id"], "title": "已过期作业",
        "due_at": (datetime.now() - timedelta(days=1)).isoformat(),
    })

    s = client.get("/api/stats").json()
    assert s["materials"] == 1
    assert s["courses"] == 1
    assert s["tasks_open"] == 2
    assert s["tasks_due_7d"] == 1
    assert s["tasks_overdue"] == 1
    assert len(s["upcoming"]) == 1
    assert s["upcoming"][0]["title"] == "2天后作业"


def test_settings_roundtrip(client):
    assert client.get("/api/settings").json() == {}

    r = client.put("/api/settings", json={"key": "semester_start", "value": "2026-08-31"})
    assert r.status_code == 200
    assert client.get("/api/settings").json()["semester_start"] == "2026-08-31"

    # 同 key 覆盖
    client.put("/api/settings", json={"key": "semester_start", "value": "2026-09-07"})
    assert client.get("/api/settings").json()["semester_start"] == "2026-09-07"


def test_settings_masks_secrets(client):
    """通用设置接口不得整串回传 AI 密钥 / 远端令牌。"""
    client.put("/api/ai/config", json={
        "llm_api_key": "sk-test-secret-1234",
        "embed_api_key": "sk-embed-secret-5678",
    })
    client.put("/api/settings", json={"key": "sync_remote_token", "value": "tok-abcdefgh"})

    s = client.get("/api/settings").json()
    assert "sk-test-secret-1234" not in s.get("llm_api_key", "")
    assert "sk-embed-secret-5678" not in s.get("embed_api_key", "")
    assert s["llm_api_key"] == "sk-••••••1234"
    assert "tok-abcdefgh" not in s.get("sync_remote_token", "")
    assert "••••" in s.get("sync_remote_token", "")


def test_csrf_origin_guard(client):
    """写操作必须与页面同源：恶意网页发的跨站请求（带 Origin）被拒绝。"""
    # 浏览器跨站请求：Origin 与 Host 不一致 → 403
    r = client.post(
        "/api/sync/token/regenerate",
        headers={"Origin": "http://evil.example.com"},
    )
    assert r.status_code == 403

    # 无 Origin（本机客户端 / 同步 httpx / 同源旧浏览器）→ 放行
    assert client.post("/api/sync/token/regenerate").status_code == 200

    # 同源（Origin 与 Host 一致）→ 放行
    assert client.post(
        "/api/sync/token/regenerate",
        headers={"Origin": "http://testserver"},
    ).status_code == 200

    # 只读请求不受影响
    assert client.get(
        "/api/health", headers={"Origin": "http://evil.example.com"}
    ).status_code == 200
