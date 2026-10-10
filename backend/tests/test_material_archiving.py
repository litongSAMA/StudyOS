"""课程资料归档子功能：章节（独立字段）· 难度 · 考试重点。

覆盖：
- CRUD：三字段创建/修改/校验（难度非法值 422）；
- 过滤：/api/materials 与 /api/search 均下推 SQL，与标签过滤正交组合；
- 章节聚合：/api/materials/chapters 自然排序（第2章 < 第10章）；
- 抽取：规则引擎从文本/文件名抽出章节/难度/考点，apply 落库保留；
- 检索：章节进 FTS 索引可被全文检索命中；
- 复习计划：难度加权 + 考试重点优先排序、时长增加；
- 同步：快照往返保留三字段；
- 导出：CSV/Markdown 带新列。
"""
from __future__ import annotations

import helpers


def _mat(client, title, **kw):
    body = {"title": title, "type": "notes", "text": "正文", **kw}
    r = client.post("/api/materials", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def test_material_crud_with_archive_fields(client):
    m = _mat(
        client, "操作系统课件",
        chapter="第3章 内存管理", difficulty="hard", exam_focus=True,
    )
    assert m["chapter"] == "第3章 内存管理"
    assert m["difficulty"] == "hard"
    assert m["exam_focus"] is True

    # PATCH 三个字段均可改
    r = client.patch(f"/api/materials/{m['id']}", json={
        "chapter": "第4章 文件系统", "difficulty": "easy", "exam_focus": False,
    })
    assert r.status_code == 200
    got = r.json()
    assert got["chapter"] == "第4章 文件系统"
    assert got["difficulty"] == "easy"
    assert got["exam_focus"] is False

    # 难度非法值：创建与修改都拒绝
    assert client.post("/api/materials", json={
        "title": "x", "difficulty": "impossible",
    }).status_code == 422
    assert client.patch(f"/api/materials/{m['id']}", json={"difficulty": "veryhard"}).status_code == 422


def test_archive_fields_filters_pushed_down(client):
    """章节/难度/考试重点过滤与标签一样在 SQL 内完成，不受 limit 截断。"""
    _mat(client, "目标-考点", chapter="第3章 内存管理", difficulty="hard", exam_focus=True)
    for i in range(24):
        _mat(client, f"噪声{i}")

    r = client.get("/api/materials", params={"exam_focus": "true", "limit": 5})
    assert [m["title"] for m in r.json()] == ["目标-考点"]
    assert r.headers["X-Total-Count"] == "1"

    r = client.get("/api/materials", params={"difficulty": "hard"})
    assert [m["title"] for m in r.json()] == ["目标-考点"]

    r = client.get("/api/materials", params={"chapter": "第3章"})
    assert [m["title"] for m in r.json()] == ["目标-考点"]

    # 检索链路同样下推
    assert client.get("/api/search", params={"q": "", "exam_focus": "true"}).json()["total"] == 1
    assert client.get("/api/search", params={"q": "", "difficulty": "hard"}).json()["total"] == 1
    assert client.get("/api/search", params={"q": "", "chapter": "内存管理"}).json()["total"] == 1

    # 与标签过滤正交组合：满足章节但不满足标签 → 0
    assert client.get(
        "/api/materials", params={"chapter": "第3章", "tag": "不存在"}
    ).json() == []


def test_chapter_facets_natural_order(client):
    """章节聚合按章节号自然排序：第2章 < 第3章 < 第10章，而非字典序。"""
    for i in (10, 2, 3):
        _mat(client, f"课件{i}", chapter=f"第{i}章")
    _mat(client, "无章节")

    facets = client.get("/api/materials/chapters").json()
    chapters = [f["chapter"] for f in facets]
    assert chapters == ["第2章", "第3章", "第10章"]
    assert {f["chapter"]: f["count"] for f in facets}["第2章"] == 1

    # 受课程/类型过滤约束
    c = client.post("/api/courses", json={"name": "机器学习"}).json()
    _mat(client, "ML 第5章", chapter="第5章", course_id=c["id"])
    ml_chapters = [f["chapter"] for f in client.get(
        "/api/materials/chapters", params={"course_id": c["id"]}
    ).json()]
    assert ml_chapters == ["第5章"]


def test_rule_extraction_fills_archive_fields(client):
    """入库文本自动抽章节/难度/考点，确认落库后字段保留。"""
    text = "操作系统课件：第6章 死锁处理（考试重点，难度较大，必考）"
    ext = client.post("/api/agent/extract", json={"text": text}).json()
    mats = ext["materials"]
    assert mats, "应识别出资料建议"
    m = mats[0]
    assert m["chapter"].startswith("第6章")
    assert m["difficulty"] == "hard"
    assert m["exam_focus"] is True

    r = client.post("/api/agent/apply", json={"tasks": [], "materials": mats, "source": "text"})
    assert r.status_code == 200
    created = r.json()["created_materials"]
    assert created[0]["chapter"].startswith("第6章")
    assert created[0]["difficulty"] == "hard"
    assert created[0]["exam_focus"] is True


def test_rule_extraction_easy_and_filename(client):
    """「基础入门」→ easy；文件名通道（第N章.pdf）同样抽到章节。"""
    ext = client.post("/api/agent/extract", json={"text": "数据结构课件：链表基础入门"}).json()
    m = ext["materials"][0]
    assert m["difficulty"] == "easy"
    assert m["exam_focus"] is False

    fname = "操作系统 第4章 文件系统.pdf"
    r = client.post("/api/materials/upload", files={"file": ("第4章文件系统.pdf", b"%PDF-fake", "application/pdf")})
    # 上传只负责返回建议（文件可能无法抽取文本，但文件名通道仍应抽出章节）
    bundle = r.json()
    titles_chapters = [
        (s["title"], s.get("chapter", ""))
        for s in bundle["extract"]["materials"]
    ]
    assert any(ch.startswith("第4章") for _, ch in titles_chapters), titles_chapters


def test_chapter_searchable_in_fts(client):
    """章节是独立字段，但同样进 FTS：搜「第9章」能命中章节资料。"""
    _mat(client, "数据库课件", chapter="第9章 事务与并发", text="事务隔离级别")
    body = client.get("/api/search", params={"q": "第9章"}).json()
    assert body["total"] >= 1
    assert any(m["chapter"] == "第9章 事务与并发" for m in body["materials"])


def test_review_plan_difficulty_weight_and_focus_priority(client):
    """复习计划：困难/考点资料 → 总时长增加、考点资料排前、范围文案带提示。"""
    c = client.post("/api/courses", json={"name": "操作系统"}).json()
    easy = _mat(client, "基础课件", course_id=c["id"], chapter="第1章", difficulty="easy")
    hard = _mat(
        client, "困难课件", course_id=c["id"], chapter="第3章",
        difficulty="hard", exam_focus=True,
    )
    exam = client.post("/api/tasks", json={
        "course_id": c["id"], "title": "操作系统期末考",
        "kind": "exam", "due_at": helpers.expect_n_days(9).isoformat(),
    }).json()

    plan = client.post("/api/review/generate", json={"exam_task_ids": [exam["id"]]}).json()
    sessions = plan["sessions"]
    first = sessions[0]
    # 考点资料排最前（掌握度 0 → 1.6× 加权，首日 3h 只装得下 hard，easy 排到后续学习块）
    assert first["material_ids"][0] == hard["id"]
    assert hard["id"] in first["study_ids"]
    assert easy["id"] in [sid for s in sessions for sid in s["study_ids"]]
    # 范围文案带考点/难度提示
    assert "考试重点" in first["scope"]
    assert "困难" in first["scope"]

    # 总时长：easy(0.8) + hard考点(1.0*1.5*1.3=1.95) = 2.75 > 每份1h 的 2.0
    total_h = sum(s["hours"] for s in sessions)
    assert total_h >= 2.7


def test_sync_roundtrip_preserves_archive_fields(client):
    """多端同步快照携带章节/难度/考点，合并后不丢。"""
    import uuid as uuid_mod
    from datetime import datetime, timezone

    m = _mat(client, "本地资料", chapter="第2章 进程", difficulty="medium", exam_focus=True)
    local = client.get(f"/api/materials/{m['id']}").json()
    suid = uuid_mod.uuid4().hex
    now_iso = lambda: datetime.now(timezone.utc).isoformat()

    snap = {
        "version": 2,
        "generated_at": now_iso(),
        "courses": [], "tasks": [], "reviews": [], "settings": [], "tombstones": [],
        "materials": [{
            "sync_uuid": suid,
            "updated_at": now_iso(),
            "created_at": now_iso(),
            "course_uuid": None,
            "title": "远端资料", "type": "notes",
            "chapter": "第2章 进程", "difficulty": "medium", "exam_focus": True,
            "source": "text", "text": "正文", "tags": [], "confidence": 1.0,
        }],
    }
    r = client.post("/api/sync/exchange", json={"snapshot": snap},
                    headers={"X-Sync-Token": "wrong"})
    assert r.status_code == 401, "未带正确令牌应拒绝"

    # 拿到本机令牌后合并
    from app.domain.sync import get_sync_token
    from app.infra.db import SessionLocal
    with SessionLocal() as db:
        token = get_sync_token(db)
    r = client.post("/api/sync/exchange", json={"snapshot": snap},
                    headers={"X-Sync-Token": token})
    assert r.status_code == 200

    got = [m for m in client.get("/api/materials").json() if m["title"] == "远端资料"]
    assert len(got) == 1
    assert got[0]["chapter"] == "第2章 进程"
    assert got[0]["difficulty"] == "medium"
    assert got[0]["exam_focus"] is True


def test_exports_include_archive_fields(client, seeded=None):
    """CSV/Markdown 导出带章节/难度/考点列。"""
    c = client.post("/api/courses", json={"name": "操作系统"}).json()
    _mat(
        client, "第3章 内存管理课件", course_id=c["id"],
        chapter="第3章 内存管理", difficulty="hard", exam_focus=True,
    )
    r = client.get("/api/export/materials/csv")
    text = r.text
    assert "ID,课程,标题,类型,章节,难度,考试重点" in text
    assert "第3章 内存管理" in text and "困难" in text and "是" in text

    r = client.get("/api/export/materials/md")
    assert "| 章节 | 难度 | 考点 |" in r.text
    assert "⭐" in r.text


def test_legacy_db_startup_migrates_archive_columns(client):
    """回归：旧库（无 chapter/difficulty/exam_focus 列）启动时先补列再跑其它迁移。

    曾出现的 bug：init_db 中 `_migrate_sync_columns` 会按 ORM 模型 SELECT Material，
    若新列还没补上就报 `no such column: materials.chapter` 导致启动失败。
    """
    from sqlalchemy import select, text

    from app.infra.db import SessionLocal, init_db
    from app.infra.models import Material

    # 模拟旧库：插入两条数据后删掉三列及其索引（旧库本就没有这些索引）。
    # 一条标题含章节/难度/考点信号 → 迁移回填应自动补抽；
    # 一条无信号 → 保持默认值。
    _mat(client, "遗留资料", chapter="第1章", difficulty="easy", exam_focus=True)
    _mat(client, "第2章 死锁处理课件（必考考点，难度较大）", tags=["死锁"])
    with SessionLocal() as db:
        for name in ("chapter", "difficulty", "exam_focus"):
            db.execute(text(f"DROP INDEX IF EXISTS ix_materials_{name}"))
        db.execute(text("ALTER TABLE materials DROP COLUMN chapter"))
        db.execute(text("ALTER TABLE materials DROP COLUMN difficulty"))
        db.execute(text("ALTER TABLE materials DROP COLUMN exam_focus"))
        db.commit()

    # 完整启动序列（与 lifespan 一致）：旧库必须能正常迁移、旧数据不丢、字段自动回填
    init_db()

    with SessionLocal() as db:
        cols = {row[1] for row in db.execute(text("PRAGMA table_info(materials)"))}
        assert {"chapter", "difficulty", "exam_focus"} <= cols
        rows = {m.title: m for m in db.execute(select(Material)).scalars().all()}
        plain = rows["遗留资料"]
        assert plain.chapter == "" and plain.difficulty == "" and plain.exam_focus is False
        rich = rows["第2章 死锁处理课件（必考考点，难度较大）"]
        assert rich.chapter.startswith("第2章"), rich.chapter
        assert rich.difficulty == "hard"
        assert rich.exam_focus is True

    # 回填只跑一次：用户手动清空字段后，重启不会被规则引擎重新覆盖
    with SessionLocal() as db:
        m = db.execute(
            select(Material).where(Material.title == "第2章 死锁处理课件（必考考点，难度较大）")
        ).scalar_one()
        m.chapter = ""
        db.commit()
    init_db()
    with SessionLocal() as db:
        m = db.execute(
            select(Material).where(Material.title == "第2章 死锁处理课件（必考考点，难度较大）")
        ).scalar_one()
        assert m.chapter == ""
