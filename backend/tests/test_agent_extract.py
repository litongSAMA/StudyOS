"""抽取 Agent：中文时间表达归一化矩阵 + 学期锚点 + 确认落库链路 + 异常路径。"""
from datetime import datetime, time

import pytest

import helpers


def _extract(client, text):
    return client.post("/api/agent/extract", json={"text": text})


@pytest.mark.parametrize("text,expected", [
    # 绝对日期
    ("数据库实验报告，11月30日晚上10点前提交",
     helpers.expect_abs(11, 30, time(22, 0))),
    ("交作业 11.30", helpers.expect_abs(11, 30)),
    ("期末大作业截止 2026-12-01 23:59",
     helpers.expect_abs(12, 1, time(23, 59), year=2026)),
    # 相对时间
    ("操作系统作业，下周五 23:59 截止",
     helpers.expect_next_weekday(4, 1, time(23, 59))),
    ("三天后交实验报告", helpers.expect_n_days(3)),
    ("明天晚上10点提交实验报告", helpers.expect_n_days(1, time(22, 0))),
    ("今晚24:00前截止", helpers.expect_n_days(1, time(0, 0))),  # 跨天
    ("大后天交课程设计", helpers.expect_n_days(3)),  # 大后天=+3 天（非后天 +2）
    # 中文时刻
    ("11月30日中午12点半答辩", helpers.expect_abs(11, 30, time(12, 30))),
])
def test_time_expression_normalization(client, text, expected):
    r = _extract(client, text)
    assert r.status_code == 200, r.text
    tasks = r.json()["tasks"]
    assert tasks, f"未能从「{text}」抽出任务"
    got = datetime.fromisoformat(tasks[0]["due_at"])
    assert got == helpers.expect_utc(expected), f"「{text}」期望 {expected}，实际 {got}"


def test_engine_is_rule_when_offline(client):
    r = _extract(client, "明天交作业")
    assert r.json()["engine"] == "rule"


def test_week_expression_needs_semester_anchor(client):
    """「第12周周三」无学期锚点 → 不产出候选（绝不静默猜错，交给人工）。"""
    r = _extract(client, "第12周周三下午2点考试")
    assert r.status_code == 200
    assert r.json()["tasks"] == []


def test_week_expression_with_global_semester_anchor(client):
    client.put("/api/settings", json={"key": "semester_start", "value": "2026-08-31"})
    r = _extract(client, "第12周周三下午2点考试")
    tasks = r.json()["tasks"]
    assert tasks
    got = datetime.fromisoformat(tasks[0]["due_at"])
    assert got == helpers.expect_utc(helpers.expect_semester_week("2026-08-31", 12, 2, time(14, 0)))
    # 置信度标注 + 依据说明（原文时间表达 + 学期锚点）
    assert tasks[0]["confidence"] > 0
    assert "第12周" in tasks[0]["due_expr"]
    assert "2026-08-31" in tasks[0]["due_expr"]
    assert "下午2点" in tasks[0]["note"]


def test_week_expression_with_course_anchor(client):
    """课程级学期锚点优先于全局设置。"""
    client.post("/api/courses", json={
        "name": "编译原理", "code": "CS401", "start_date": "2026-08-31",
    })
    r = _extract(client, "编译原理第12周周三下午2点考试")
    tasks = r.json()["tasks"]
    assert tasks
    assert tasks[0]["course"] == "编译原理"
    got = datetime.fromisoformat(tasks[0]["due_at"])
    assert got == helpers.expect_utc(helpers.expect_semester_week("2026-08-31", 12, 2, time(14, 0)))


def test_extract_no_task_for_plain_text(client):
    """有时间但没有任务动作词/DDL 词 → 不算任务（如「明天天气不错」）。"""
    r = _extract(client, "明天天气不错，适合出门")
    assert r.json()["tasks"] == []


def test_extract_empty_text_422(client):
    assert _extract(client, "").status_code == 422
    assert _extract(client, "   ").status_code == 422


def test_extract_apply_full_chain(client):
    """核心链路：抽取 → 确认落库 → 任务可见 + 资料可检索（FTS 同步）。"""
    client.post("/api/courses", json={"name": "操作系统", "code": "CS301"})

    r = _extract(client, "操作系统实验三报告，下周三晚上10点前提交")
    assert r.status_code == 200
    ext = r.json()
    assert ext["engine"] == "rule"
    assert len(ext["tasks"]) == 1
    t = ext["tasks"][0]
    assert t["course"] == "操作系统"
    assert t["kind"] == "report"
    assert datetime.fromisoformat(t["due_at"]) == helpers.expect_utc(
        helpers.expect_next_weekday(2, 1, time(22, 0))
    )

    # 确认建议 → 落库（任务 + 资料一起）
    r2 = client.post("/api/agent/apply", json={
        "tasks": ext["tasks"], "materials": ext["materials"], "source": "text",
    })
    assert r2.status_code == 200
    applied = r2.json()
    assert len(applied["created_tasks"]) == 1
    assert len(applied["created_materials"]) == 1

    tasks = client.get("/api/tasks").json()
    assert len(tasks) == 1
    assert tasks[0]["title"] == t["title"]

    # apply 落库的资料已同步进 FTS，可检索
    body = client.get("/api/search", params={"q": "实验三"}).json()
    assert body["total"] >= 1


def test_apply_creates_unknown_course(client):
    """建议中的新课程名 → 自动创建并回报。"""
    r = client.post("/api/agent/apply", json={
        "tasks": [{
            "title": "编译原理课程设计", "kind": "homework",
            "due_at": helpers.expect_n_days(5).isoformat(),
            "course": "编译原理", "confidence": 0.9,
        }],
        "materials": [],
    })
    assert r.status_code == 200
    assert "编译原理" in r.json()["matched_courses"]
    names = [c["name"] for c in client.get("/api/courses").json()]
    assert "编译原理" in names


def test_apply_task_without_due_422(client):
    """无截止时间的任务建议 → 拒绝落库，绝不脏数据。"""
    r = client.post("/api/agent/apply", json={
        "tasks": [{"title": "没有时间的任务", "due_at": None}],
    })
    assert r.status_code == 422
    assert client.get("/api/tasks").json() == []
