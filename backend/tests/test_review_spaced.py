"""间隔重复 + 掌握程度：评分调度（SM-2 变体）、状态总览、到期队列、
计划生成加权与预排检查点、状态清理、多端同步往返。"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

import helpers
from app.infra.config import settings
from app.infra.db import Base, SessionLocal
from app.infra.models import MaterialReview
from app.domain.spacedrep import (
    INTERVALS,
    apply_rating,
    next_ease,
    next_interval,
    study_weight,
)
from app.domain.sync import apply_snapshot, build_snapshot
from app.infra.timeutil import utcnow


# ---------------------------------------------------------------------------
# 调度公式（纯函数，精确值）
# ---------------------------------------------------------------------------


def test_scheduler_intervals():
    # 间隔阶梯：第 1/2/3/4/5/6 次成功 → 1/2/4/7/14/30 天，之后封顶
    assert [next_interval(n, "good") for n in range(1, 8)] == [1.0, 2.0, 4.0, 7.0, 14.0, 30.0, 30.0]
    # hard/easy 微调；hard 下限 1 天；again 一律 1 天
    assert next_interval(2, "easy") == round(2 * 1.3, 1)   # 2.6
    assert next_interval(2, "hard") == round(2 * 0.7, 1)   # 1.4
    assert next_interval(1, "hard") == 1.0
    assert next_interval(0, "again") == 1.0
    assert INTERVALS == (1, 2, 4, 7, 14, 30)


def test_scheduler_ease_and_mastery():
    # SM-2 难度系数：good 不变（q=4 增量 0）、easy +0.1、hard -0.14、again -0.2（下限 1.3）
    assert next_ease(2.5, "good") == 2.5
    assert next_ease(2.5, "easy") == pytest.approx(2.6)
    assert next_ease(2.5, "hard") == pytest.approx(2.36)
    assert next_ease(2.5, "again") == 2.3
    assert next_ease(1.35, "again") == 1.3
    assert next_ease(1.3, "again") == 1.3
    # 掌握程度 clamp 到 [0, 1]
    assert apply_rating(0.9, 3, 0, 2.5, "easy")["mastery"] == 1.0
    assert apply_rating(0.1, 3, 0, 2.5, "again")["mastery"] == 0.0
    # 掌握系数：未掌握 1.6×、完全掌握 1.0×
    assert study_weight(0.0) == pytest.approx(1.6)
    assert study_weight(1.0) == pytest.approx(1.0)
    assert study_weight(0.5) == pytest.approx(1.3)


def test_apply_rating_lapse_resets_ladder():
    now = utcnow()
    up = apply_rating(0.5, 3, 0, 2.5, "again", now)
    assert up["repetitions"] == 0 and up["lapses"] == 1
    assert up["interval_days"] == 1.0
    assert up["due_at"] == now + timedelta(days=1)
    up2 = apply_rating(0.5, 3, 0, 2.5, "easy", now)
    assert up2["repetitions"] == 4
    assert up2["interval_days"] == round(7 * 1.3, 1)  # 第 4 次成功 → 7 天 × easy 1.3


# ---------------------------------------------------------------------------
# 评分 API 与状态总览
# ---------------------------------------------------------------------------


def _seed(client, days=10, name="操作系统"):
    c = client.post("/api/courses", json={"name": name}).json()
    m1 = client.post("/api/materials", json={
        "course_id": c["id"], "title": f"{name}课件：进程调度",
        "type": "courseware", "text": "进程调度", "tags": ["进程"],
    }).json()
    m2 = client.post("/api/materials", json={
        "course_id": c["id"], "title": f"{name}复习笔记",
        "type": "notes", "text": "内存管理", "tags": ["内存"],
    }).json()
    exam = client.post("/api/tasks", json={
        "course_id": c["id"], "title": f"{name}期中考试",
        "kind": "exam", "due_at": helpers.expect_n_days(days).isoformat(),
    }).json()
    return c, m1, m2, exam


def test_rate_creates_state_and_advances(client):
    _, m1, _, _ = _seed(client)
    r = client.post("/api/review/rate", json={"material_id": m1["id"], "rating": "good"})
    assert r.status_code == 200
    st = r.json()
    assert st["mastery"] == pytest.approx(0.15)
    assert st["repetitions"] == 1 and st["interval_days"] == 1.0
    assert st["last_rating"] == "good" and st["due_at"] and not st["is_due"]

    st = client.post("/api/review/rate", json={"material_id": m1["id"], "rating": "good"}).json()
    assert st["mastery"] == pytest.approx(0.3)
    assert st["repetitions"] == 2 and st["interval_days"] == 2.0

    # again → 遗忘：连续成功清零、掌握度回落（clamp 到 0）
    st = client.post("/api/review/rate", json={"material_id": m1["id"], "rating": "again"}).json()
    assert st["repetitions"] == 0 and st["lapses"] == 1
    assert st["mastery"] == 0.0
    assert st["interval_days"] == 1.0


def test_rate_errors(client):
    _, m1, _, _ = _seed(client)
    assert client.post("/api/review/rate", json={"material_id": m1["id"], "rating": "so-so"}).status_code == 422
    assert client.post("/api/review/rate", json={"material_id": 999, "rating": "good"}).status_code == 404


def test_states_list_and_reset(client):
    _, m1, m2, _ = _seed(client)
    client.post("/api/review/rate", json={"material_id": m1["id"], "rating": "good"})

    states = client.get("/api/review/states").json()
    by_id = {s["material_id"]: s for s in states}
    assert set(by_id) == {m1["id"], m2["id"]}
    assert by_id[m1["id"]]["mastery"] == pytest.approx(0.15)
    assert by_id[m1["id"]]["material_title"] == m1["title"]
    assert by_id[m1["id"]]["course_name"] == "操作系统"
    # 没评过分的资料按「未复习」出现
    assert by_id[m2["id"]]["mastery"] == 0.0 and by_id[m2["id"]]["repetitions"] == 0

    # 课程过滤
    other = client.post("/api/courses", json={"name": "机器学习"}).json()
    om = client.post("/api/materials", json={
        "course_id": other["id"], "title": "神经网络", "type": "courseware",
    }).json()
    filtered = client.get(f"/api/review/states?course_id={other['id']}").json()
    assert [s["material_id"] for s in filtered] == [om["id"]]

    # 重置状态
    assert client.delete(f"/api/review/state/{m1['id']}").status_code == 204
    states = client.get("/api/review/states").json()
    st = next(s for s in states if s["material_id"] == m1["id"])
    assert st["mastery"] == 0.0 and st["repetitions"] == 0
    assert client.delete(f"/api/review/state/{m1['id']}").status_code == 204  # 幂等
    assert client.delete("/api/review/state/999").status_code == 404


def test_states_due_flag(client):
    _, m1, _, _ = _seed(client)
    client.post("/api/review/rate", json={"material_id": m1["id"], "rating": "good"})
    states = client.get("/api/review/states").json()
    assert not next(s for s in states if s["material_id"] == m1["id"])["is_due"]

    # 把到期时刻改到昨天（模拟间隔已到）
    with SessionLocal() as db:
        row = db.execute(
            select(MaterialReview).where(MaterialReview.material_id == m1["id"])
        ).scalar_one()
        row.due_at = utcnow() - timedelta(days=1)
        db.commit()
    states = client.get("/api/review/states").json()
    st = next(s for s in states if s["material_id"] == m1["id"])
    assert st["is_due"] and st["overdue_days"] == 1


def test_material_delete_clears_state(client):
    _, m1, _, _ = _seed(client)
    client.post("/api/review/rate", json={"material_id": m1["id"], "rating": "good"})
    assert client.delete(f"/api/materials/{m1['id']}").status_code == 204
    assert all(s["material_id"] != m1["id"] for s in client.get("/api/review/states").json())


def test_rate_history_bounded(client):
    _, m1, _, _ = _seed(client)
    for _ in range(25):
        client.post("/api/review/rate", json={"material_id": m1["id"], "rating": "good"})
    with SessionLocal() as db:
        row = db.execute(
            select(MaterialReview).where(MaterialReview.material_id == m1["id"])
        ).scalar_one()
        assert len(row.history) == 20
        assert row.history[-1]["rating"] == "good"


# ---------------------------------------------------------------------------
# 计划生成：掌握程度加权 + 间隔重复
# ---------------------------------------------------------------------------


def test_generate_embeds_spaced_checkpoints(client):
    _, m1, m2, exam = _seed(client)
    plan = client.post("/api/review/generate", json={"exam_task_ids": [exam["id"]]}).json()
    sessions = plan["sessions"]

    # 首次学习日在今天，间隔复习检查点落在 +1 / +3 / +7 天
    by_date = {s["date"]: s for s in sessions}
    assert date.today().isoformat() in by_date
    for off in (1, 3, 7):
        d = (date.today() + timedelta(days=off)).isoformat()
        assert d in by_date, f"第 +{off} 天应有间隔复习检查点"
        assert {m1["id"], m2["id"]} <= set(by_date[d]["spaced_ids"])
        assert "间隔复习" in by_date[d]["scope"]
    # 今天的新学块不受影响
    today_s = by_date[date.today().isoformat()]
    assert {m1["id"], m2["id"]} <= set(today_s["study_ids"])
    assert "掌握度 0%" in today_s["scope"]


def test_generate_schedules_due_reviews_first(client):
    _, m1, _, exam = _seed(client)
    client.post("/api/review/rate", json={"material_id": m1["id"], "rating": "good"})
    with SessionLocal() as db:
        row = db.execute(
            select(MaterialReview).where(MaterialReview.material_id == m1["id"])
        ).scalar_one()
        row.due_at = utcnow() - timedelta(days=1)  # 已到期
        db.commit()

    plan = client.post("/api/review/generate", json={"exam_task_ids": [exam["id"]]}).json()
    today_s = next(s for s in plan["sessions"] if s["date"] == date.today().isoformat())
    assert m1["id"] in today_s["due_ids"]
    assert today_s["material_ids"][0] == m1["id"]  # 到期复习排最前（检索练习先行）
    assert "到期复习" in today_s["scope"]
    # 已排过间隔的资料不再重复预排检查点
    assert m1["id"] not in today_s["spaced_ids"]
    assert all(m1["id"] not in s["spaced_ids"] for s in plan["sessions"])


def test_generate_weights_by_mastery(client):
    # 课程 A：全部资料评 4 次 easy → 掌握度 1.0（已掌握）
    ca, ma1, ma2, exam_a = _seed(client, days=12, name="操作系统")
    for m in (ma1, ma2):
        for _ in range(4):
            client.post("/api/review/rate", json={"material_id": m["id"], "rating": "easy"})
    # 课程 B：全新未复习，DDL 更晚
    cb, mb1, mb2, exam_b = _seed(client, days=13, name="机器学习")

    plan = client.post("/api/review/generate", json={
        "exam_task_ids": [exam_a["id"], exam_b["id"]],
    }).json()
    a_sessions = [s for s in plan["sessions"] if s["course"] == "操作系统"]
    b_sessions = [s for s in plan["sessions"] if s["course"] == "机器学习"]
    assert a_sessions and b_sessions
    # 已掌握的课程：不再排初次学习，只按间隔到期复习，掌握度 100%
    assert all(s["mastery"] == 1.0 for s in a_sessions)
    assert all(not s["study_ids"] for s in a_sessions)
    assert all(s["due_ids"] for s in a_sessions)
    # 未复习的课程：正常排学习块，掌握度 0%
    assert all(s["mastery"] == 0.0 for s in b_sessions)
    assert any(s["study_ids"] for s in b_sessions)


def test_generate_hours_shrink_with_mastery(client):
    """掌握程度越高，同一门课的总学习时长越少（未掌握 1.6× → 已掌握 1.0×）。"""
    c1, m1, m2, exam1 = _seed(client, days=12, name="操作系统")
    # 一次 good 后掌握度 0.15：总时长 = 2 × 1.0 × (1.6 - 0.6×0.15) ≈ 3.02 < 未复习的 3.2
    client.post("/api/review/rate", json={"material_id": m1["id"], "rating": "good"})
    client.post("/api/review/rate", json={"material_id": m2["id"], "rating": "good"})
    plan = client.post("/api/review/generate", json={"exam_task_ids": [exam1["id"]]}).json()
    study_hours = sum(s["hours"] - 0.5 * (len(s["due_ids"]) + len(s["spaced_ids"])) for s in plan["sessions"])
    assert 3.0 <= study_hours < 3.2  # 学习时长比全未复习（3.2h）少


# ---------------------------------------------------------------------------
# 多端同步：间隔重复状态随快照往返
# ---------------------------------------------------------------------------


def _device_b():
    """模拟第二台设备：独立库文件 + FTS/语义表（复用同一套同步钩子与合并函数）。"""
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


def test_sync_roundtrip_review_states(client):
    _, m1, _, exam = _seed(client)
    client.post("/api/review/rate", json={"material_id": m1["id"], "rating": "good"})
    client.post("/api/review/generate", json={"exam_task_ids": [exam["id"]]})

    SessionB = _device_b()
    with SessionLocal() as db:
        snap = build_snapshot(db, include_files=False)
    assert any(it.get("mastery") for it in snap["material_reviews"])

    with SessionB() as db2:
        stats = apply_snapshot(db2, snap)
        assert stats["created"] >= 1
        rows = db2.execute(select(MaterialReview)).scalars().all()
        assert len(rows) == 1
        r = rows[0]
        assert r.mastery == pytest.approx(0.15)
        assert r.repetitions == 1 and r.last_rating == "good"
        assert r.due_at is not None
        # 计划 session 的 study/spaced 引用按 uuid 重映射后仍能对上本地资料
        from app.infra.models import Material, ReviewPlan
        mats = db2.execute(select(Material)).scalars().all()
        assert len(mats) == 2
        plan = db2.execute(select(ReviewPlan)).scalars().one()
        assert any(
            m.id in (s.get("study_ids") or []) or m.id in (s.get("spaced_ids") or [])
            for m in mats for s in plan.sessions
        )
