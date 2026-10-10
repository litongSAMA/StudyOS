"""多端同步：令牌、快照交换收敛、LWW 冲突解决、墓碑传播、备份/恢复。"""
from __future__ import annotations

import io
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from app.infra.config import settings
from app.infra.db import Base, SessionLocal
from app.infra.models import Course, Material, Note, ReviewPlan, Task, Tombstone
from app.domain.sync import apply_snapshot, build_snapshot
from app.infra.timeutil import utcnow


# ---------------------------------------------------------------------------
# 第二台「设备」：独立 SQLite + 会话工厂（复用同一套同步钩子与合并函数）
# ---------------------------------------------------------------------------


def _device():
    """模拟第二台设备：独立引擎/库文件，含 FTS 与语义缓存表。"""
    path = settings.data_dir / f"device-b-{datetime.now().timestamp()}.db"
    eng = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    with eng.begin() as conn:
        conn.execute(text(
            "CREATE VIRTUAL TABLE IF NOT EXISTS materials_fts USING fts5("
            "title, text, tags, raw UNINDEXED, tokenize='unicode61')"
        ))
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS semantic_vectors ("
            "material_id INTEGER PRIMARY KEY, model TEXT NOT NULL DEFAULT '',"
            "dim INTEGER NOT NULL DEFAULT 0, nchunks INTEGER NOT NULL DEFAULT 0,"
            "vecs BLOB NOT NULL, text_hash TEXT NOT NULL DEFAULT '')"
        ))
    return sessionmaker(bind=eng, autoflush=False, expire_on_commit=False)


def _seed(client):
    c = client.post("/api/courses", json={"name": "操作系统"}).json()
    m = client.post("/api/materials", json={
        "course_id": c["id"], "title": "死锁课件",
        "type": "courseware", "text": "死锁四个必要条件与银行家算法。",
        "tags": ["死锁"],
    }).json()
    t = client.post("/api/tasks", json={
        "course_id": c["id"], "title": "银行家算法作业",
        "due_at": (datetime.now() + timedelta(days=3)).isoformat(),
    }).json()
    client.post("/api/review/generate", json={"exam_task_ids": [t["id"]]})
    return c, m, t


def _token(client) -> str:
    """取本机明文令牌：只能通过显式披露接口（status 只回掩码）。"""
    return client.post("/api/sync/token/reveal").json()["token"]


def test_sync_status_and_token(client):
    s = client.get("/api/sync/status").json()
    assert s["device_id"]
    # status 只回掩码 + 指纹，绝不明文回传令牌
    assert "token" not in s
    assert s["token_masked"].endswith(_token(client)[-4:])
    assert len(s["token_fingerprint"]) == 8
    assert s["counts"]["courses"] == 0

    old = _token(client)
    r = client.post("/api/sync/token/regenerate").json()
    assert "token" not in r and r["token_fingerprint"]
    new = _token(client)
    assert new and new != old
    assert r["token_fingerprint"] == client.get("/api/sync/status").json()["token_fingerprint"]

    # 无令牌访问快照 → 401
    assert client.get("/api/sync/snapshot").status_code == 401
    r = client.get("/api/sync/snapshot", headers={"X-Sync-Token": new})
    assert r.status_code == 200
    assert r.json()["courses"] == []


def test_token_never_exposed_passively(client, lan_client):
    """令牌不得从任何被动查询接口泄露；明文披露仅限本机且需显式 POST。"""
    token = _token(client)

    # 通用设置接口：sync_token 只回掩码
    client.get("/api/sync/status")  # 确保令牌已生成落库
    settings_body = client.get("/api/settings").json()
    assert token not in str(settings_body)
    if "sync_token" in settings_body:
        assert settings_body["sync_token"] != token
        assert "•" in settings_body["sync_token"]

    # 局域网内其它设备：status 可看（不含明文），但明文披露/重生成被拒
    lan_status = lan_client.get("/api/sync/status").json()
    assert token not in str(lan_status)
    assert lan_status["client_local"] is False
    assert lan_status["token_reveal_allowed"] is False
    r = lan_client.post("/api/sync/token/reveal")
    assert r.status_code == 403
    assert "这台设备" in r.json()["detail"]
    assert lan_client.post("/api/sync/token/regenerate").status_code == 403

    # 本机披露：返回明文 + 风险提示（知情同意）
    revealed = client.post("/api/sync/token/reveal").json()
    assert revealed["token"] == token
    assert "完整读写权限" in revealed["warning"]


def test_reveal_allowed_when_remote_admin_enabled(client, lan_client):
    """反向代理/远程访问场景：ALLOW_REMOTE_ADMIN=1 显式放行远程披露。"""
    from app.infra.config import settings as app_settings

    app_settings.allow_remote_admin = True
    try:
        r = lan_client.post("/api/sync/token/reveal")
        assert r.status_code == 200
        assert r.json()["token"] == _token(client)
        assert lan_client.get("/api/sync/status").json()["token_reveal_allowed"] is True
    finally:
        app_settings.allow_remote_admin = False


def test_regenerate_rejected_when_token_fixed_by_env(client, monkeypatch):
    """SYNC_TOKEN 固定部署时，重新生成会写库但不生效 → 明确 422 而非静默无效。"""
    monkeypatch.setenv("SYNC_TOKEN", "fixed-token-for-test")
    st = client.get("/api/sync/status").json()
    assert st["token_source"] == "env"
    assert st["token_masked"].endswith("test") and "fixed-token" not in st["token_masked"]
    r = client.post("/api/sync/token/regenerate")
    assert r.status_code == 422
    assert "环境变量" in r.json()["detail"]
    assert _token(client) == "fixed-token-for-test"


def test_exchange_converges_two_devices(client):
    c, m, t = _seed(client)
    B = _device()

    # A 全量快照 → B 合并
    with SessionLocal() as dbA:
        suid_task = dbA.execute(select(Task).where(Task.id == t["id"])).scalar_one().sync_uuid
        snapA = build_snapshot(dbA)
    with B() as dbB:
        stats = apply_snapshot(dbB, snapA)
    assert stats["created"] == 4  # 课程 + 资料 + 任务 + 复习计划

    with B() as dbB:
        task_b = dbB.execute(select(Task)).scalar_one()
        assert task_b.title == "银行家算法作业"
        assert task_b.sync_uuid == suid_task  # 跨端 uuid 一致

    # B 修改任务（B 的 updated_at 更新）→ 回传快照 → A 合并后收敛
    with B() as dbB:
        task_b = dbB.execute(select(Task)).scalar_one()
        task_b.done = True
        dbB.commit()
        snapB = build_snapshot(dbB)
    with SessionLocal() as dbA:
        stats = apply_snapshot(dbA, snapB)
    assert stats["updated"] >= 1
    r = client.get("/api/tasks", params={"done": True})
    assert [x["title"] for x in r.json()] == ["银行家算法作业"]

    # 双向各再同步一次应稳定无变化（时间戳相等 → 全部跳过）
    with SessionLocal() as dbA:
        snapA2 = build_snapshot(dbA)
    with B() as dbB:
        assert apply_snapshot(dbB, snapA2)["skipped"] >= 4
    with B() as dbB:
        snapB2 = build_snapshot(dbB)
    with SessionLocal() as dbA:
        assert apply_snapshot(dbA, snapB2)["skipped"] >= 4


def test_lww_newer_wins_older_skipped(client):
    t = client.post("/api/tasks", json={
        "title": "LWW 任务", "due_at": (datetime.now() + timedelta(days=1)).isoformat(),
    }).json()
    with SessionLocal() as db:
        task = db.execute(select(Task)).scalar_one()
        suid = task.sync_uuid
        base = build_snapshot(db)

    # 旧时间戳（本地 updated_at - 1 天）→ 跳过
    old_snap = {
        "version": 1, "generated_at": None, "device_id": "x",
        "courses": [], "materials": [], "reviews": [], "settings": [], "tombstones": [],
        "tasks": [{**next(x for x in base["tasks"] if x["sync_uuid"] == suid),
                   "title": "旧修改",
                   "updated_at": (utcnow() - timedelta(days=1)).isoformat()}],
    }
    with SessionLocal() as db:
        assert apply_snapshot(db, old_snap)["skipped"] == 1
        assert db.execute(select(Task)).scalar_one().title == "LWW 任务"

    # 新时间戳 → 应用
    new_snap = {
        "version": 1, "generated_at": None, "device_id": "x",
        "courses": [], "materials": [], "reviews": [], "settings": [], "tombstones": [],
        "tasks": [{**next(x for x in base["tasks"] if x["sync_uuid"] == suid),
                   "title": "新修改",
                   "updated_at": (utcnow() + timedelta(seconds=5)).isoformat()}],
    }
    with SessionLocal() as db:
        assert apply_snapshot(db, new_snap)["updated"] == 1
        assert db.execute(select(Task)).scalar_one().title == "新修改"


def test_tombstone_propagation_and_resurrection(client):
    t = client.post("/api/tasks", json={
        "title": "将被删除的任务", "due_at": (datetime.now() + timedelta(days=1)).isoformat(),
    }).json()
    assert client.delete(f"/api/tasks/{t['id']}").status_code == 204
    with SessionLocal() as db:
        assert db.execute(select(Task)).scalars().all() == []
        tombstones = db.execute(select(Tombstone)).scalars().all()
        assert len(tombstones) == 1 and tombstones[0].entity == "task"
        suid = tombstones[0].sync_uuid
        snap = build_snapshot(db)
    assert snap["tombstones"]
    # 把删除时间设为「一小时后」，保证比 B 上旧任务的修改时间新
    snap["tombstones"][0]["deleted_at"] = (utcnow() + timedelta(hours=1)).isoformat()

    # 墓碑同步到设备 B → B 上同名任务被删除
    B = _device()
    with B() as dbB:
        dbB.add(Task(sync_uuid=suid, title="B 上的旧任务",
                     due_at=utcnow() - timedelta(days=2)))
        dbB.commit()
        stats = apply_snapshot(dbB, snap)
    assert stats["deleted"] == 1
    with B() as dbB:
        assert dbB.execute(select(Task)).scalars().all() == []

    # 重建（updated_at 比墓碑新）→ 复活，不被旧墓碑误删
    resurrect = {
        "version": 1, "generated_at": None, "device_id": "y",
        "courses": [], "materials": [], "reviews": [], "settings": [], "tombstones": [],
        "tasks": [{
            "sync_uuid": suid, "title": "重新创建的任务",
            "kind": "homework", "due_at": utcnow().isoformat(),
            "done": False, "remind_offsets_h": [72, 24, 2],
            "note": "", "source_text": "", "source_material_uuid": None,
            "course_uuid": None, "confidence": 1.0,
            "updated_at": (utcnow() + timedelta(hours=2)).isoformat(),
            "created_at": utcnow().isoformat(),
        }],
    }
    with B() as dbB:
        stats = apply_snapshot(dbB, resurrect)
    assert stats["created"] == 1
    with B() as dbB:
        assert dbB.execute(select(Task)).scalar_one().title == "重新创建的任务"


def test_notes_sync_roundtrip_and_tombstone(client):
    """笔记进同步快照：跨端迁移、资料引用重映射、删除墓碑传播。"""
    c, m, t = _seed(client)
    note = client.post("/api/notes", json={
        "material_id": m["id"], "title": "死锁重点笔记", "mode": "keypoints",
        "summary": "死锁四个必要条件。",
        "points": [
            {"point": "互斥、请求保持、不可剥夺、循环等待", "importance": "high"},
            {"point": "银行家算法", "importance": "medium"},
        ],
    }).json()

    # A 全量快照 → 设备 B：笔记连同资料引用一并迁移
    with SessionLocal() as dbA:
        snapA = build_snapshot(dbA)
    B = _device()
    with B() as dbB:
        stats = apply_snapshot(dbB, snapA)
    assert stats["created"] == 5  # 课程 + 资料 + 任务 + 复习计划 + 笔记
    with B() as dbB:
        n = dbB.execute(select(Note)).scalar_one()
        assert n.title == "死锁重点笔记"
        assert n.mode == "keypoints"
        assert n.points == [
            {"point": "互斥、请求保持、不可剥夺、循环等待", "importance": "high"},
            {"point": "银行家算法", "importance": "medium"},
        ]
        mat_b = dbB.execute(select(Material)).scalar_one()
        assert n.material_id == mat_b.id  # 本地自增 id 不同 → 按 uuid 重映射

    # A 端删除笔记 → 墓碑随快照传播，B 端笔记被删除
    assert client.delete(f"/api/notes/{note['id']}").status_code == 204
    with SessionLocal() as dbA:
        assert dbA.execute(select(Tombstone).where(Tombstone.entity == "note")).scalar_one()
        snapA2 = build_snapshot(dbA)
    with B() as dbB:
        stats = apply_snapshot(dbB, snapA2)
        assert stats["deleted"] == 1
        assert dbB.execute(select(Note)).scalars().all() == []

    # 同步状态计数包含笔记
    assert "notes" in client.get("/api/sync/status").json()["counts"]


def test_settings_sync_and_local_keys_excluded(client):
    client.put("/api/settings", json={"key": "semester_start", "value": "2026-08-31"})
    with SessionLocal() as db:
        snap = build_snapshot(db)
    keys = [s["key"] for s in snap["settings"]]
    assert "semester_start" in keys
    assert not any(k.startswith("sync_") for k in keys)

    B = _device()
    from app.infra.models import AppSetting

    with B() as dbB:
        apply_snapshot(dbB, snap)
        assert dbB.get(AppSetting, "semester_start").value == "2026-08-31"


def test_backup_and_restore_roundtrip(client):
    c, m, t = _seed(client)
    # 给资料挂一个真实文件
    files_dir = settings.data_dir / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    f = files_dir / "demo.txt"
    f.write_text("死锁 银行家算法 复习要点", encoding="utf-8")
    with SessionLocal() as db:
        mat = db.get(Material, m["id"])
        mat.file_path = str(f)
        mat.file_name = "demo.txt"
        db.commit()

    # 备份/恢复与 snapshot、exchange 一样受令牌保护：无令牌 → 401
    token = _token(client)
    assert client.get("/api/sync/backup").status_code == 401
    r = client.get("/api/sync/backup", headers={"X-Sync-Token": token})
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    backup_bytes = r.content
    assert len(backup_bytes) > 100

    # 清空本端（模拟数据丢失）
    with SessionLocal() as db:
        for table in ("materials", "tasks", "review_plans", "courses", "app_settings", "tombstones"):
            db.execute(text(f"DELETE FROM {table}"))
        db.execute(text("DELETE FROM materials_fts"))
        db.execute(text("DELETE FROM semantic_vectors"))
        db.commit()
    assert client.get("/api/courses").json() == []

    # 恢复：无令牌 → 401。数据清空连带令牌丢失，但披露接口会重新生成令牌，
    # 前端恢复时正是「先取令牌、再带头上传」——这里复现同一顺序。
    assert client.post("/api/sync/restore",
                       files={"file": ("backup.zip", io.BytesIO(backup_bytes), "application/zip")}).status_code == 401
    token = _token(client)
    r = client.post("/api/sync/restore",
                    files={"file": ("backup.zip", io.BytesIO(backup_bytes), "application/zip")},
                    headers={"X-Sync-Token": token})
    assert r.status_code == 200, r.text
    stats = r.json()["stats"]
    assert stats["created"] >= 4

    courses = client.get("/api/courses").json()
    assert [x["name"] for x in courses] == ["操作系统"]
    mats = client.get("/api/materials").json()
    assert mats[0]["title"] == "死锁课件"
    assert mats[0]["file_name"] == "demo.txt"
    assert Path(mats[0]["file_path"]).exists()
    tasks = client.get("/api/tasks").json()
    assert tasks[0]["title"] == "银行家算法作业"
    assert client.get("/api/review").json()

    # 恢复后检索仍可用（FTS 重建）
    r = client.get("/api/search", params={"q": "死锁"})
    assert r.json()["total"] >= 1


def test_restore_rejects_bad_zip(client):
    r = client.post("/api/sync/restore",
                    files={"file": ("bad.zip", io.BytesIO(b"not a zip"), "application/zip")},
                    headers={"X-Sync-Token": _token(client)})
    assert r.status_code == 422
    assert "无效" in r.json()["detail"]


def test_exchange_endpoint_requires_token_and_applies(client):
    _seed(client)
    with SessionLocal() as db:
        snap = build_snapshot(db)
    assert client.post("/api/sync/exchange", json={"snapshot": snap}).status_code == 401

    B = _device()
    with B() as dbB:
        pass  # B 为空
    # 用本端令牌调用 exchange：远端数据合并进来后回传快照
    token = _token(client)
    r = client.post("/api/sync/exchange", json={"snapshot": snap},
                    headers={"X-Sync-Token": token})
    assert r.status_code == 200
    body = r.json()
    assert body["applied"]["created"] >= 4 or body["applied"]["skipped"] >= 4
    assert body["snapshot"]["courses"]


def test_sync_now_endpoint_plumbing(client, monkeypatch):
    _seed(client)
    B = _device()
    token = _token(client)

    def fake_post(url, json=None, headers=None, timeout=None, trust_env=None):
        assert headers["X-Sync-Token"] == token
        # 局域网对端必须直连：不能被本机 HTTP_PROXY 截走（见 app/netutil.py）
        assert trust_env is False, "局域网同步应固定 trust_env=False"
        with B() as dbB:
            applied = apply_snapshot(dbB, json["snapshot"])
            snapB = build_snapshot(dbB, include_files=json["include_files"])
        return _FakeResp({"applied": applied, "snapshot": snapB})

    monkeypatch.setattr("app.domain.sync.httpx.post", fake_post)
    r = client.post("/api/sync/now", json={
        "remote_url": "http://192.168.1.10:8800", "remote_token": token,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["pushed"]["created"] >= 4
    # 远端配置被记住
    status = client.get("/api/sync/status").json()
    assert status["remote_url"] == "http://192.168.1.10:8800"
    assert status["remote_token_set"] is True
    client.post("/api/sync/remote/clear")
    status = client.get("/api/sync/status").json()
    assert status["remote_url"] == "" and status["remote_token_set"] is False


class _FakeResp:
    def __init__(self, payload):
        self.status_code = 200
        self._payload = payload

    def json(self):
        return self._payload
