"""错题归集（错题本）：CRUD 与自动抽取、过滤检索与分页、重练自评与掌握判定、
聚合与统计、Markdown 导出、多端同步往返。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.infra.config import settings
from app.infra.db import Base, SessionLocal
from app.infra.models import Course, WrongQuestion
from app.domain.sync import apply_snapshot, build_snapshot


def _course(client, name="操作系统"):
    return client.post("/api/courses", json={"name": name}).json()


def _wrong(client, course_id=None, title="银行家算法错题", **over):
    body = {
        "course_id": course_id,
        "title": title,
        "question": "某系统有 5 个进程，判断是否存在死锁。",
        "my_answer": "安全",
        "answer": "不安全，存在死锁",
        "reason": "漏算了一个资源",
        "source": "2024 期末试卷",
        "tags": ["死锁"],
    }
    body.update(over)
    return client.post("/api/wrongbook", json=body)


def test_create_with_auto_extract_and_validation(client):
    """章节/难度留空自动按标题+题干规则抽取；显式值保留；非法难度 422；课程 404。"""
    c = _course(client)
    r = _wrong(client, c["id"], title="第3章 死锁（难点）", chapter="", difficulty="")
    assert r.status_code == 201, r.text
    w = r.json()
    assert w["course_name"] == "操作系统"
    assert w["chapter"] == "第3章 死锁"          # 章节自动抽取（括号截断）
    assert w["difficulty"] == "hard"             # 「难点」→ hard
    assert w["status"] == "open"
    assert w["wrong_count"] == 1

    # 显式值不被覆盖
    r = _wrong(client, c["id"], title="另一题", chapter="第2章", difficulty="easy")
    assert r.json()["chapter"] == "第2章" and r.json()["difficulty"] == "easy"

    # 非法难度 422 / 课程不存在 404 / 空标题回落
    assert _wrong(client, c["id"], difficulty="bogus").status_code == 422
    assert _wrong(client, 9999).status_code == 404
    r = _wrong(client, c["id"], title="   ")
    assert r.status_code == 201 and r.json()["title"] == "未命名错题"


def test_wrongbook_crud_and_validation(client):
    c = _course(client)
    w = _wrong(client, c["id"]).json()

    # 单条读取 + 列表
    assert client.get(f"/api/wrongbook/{w['id']}").json()["title"] == w["title"]
    assert [x["id"] for x in client.get("/api/wrongbook").json()] == [w["id"]]

    # 编辑：改题干/答案/状态/标签（去重归一化）
    r = client.patch(f"/api/wrongbook/{w['id']}", json={
        "question": "更新后的题干。", "status": "done",
        "tags": ["死锁", "死锁", " 银行家 ", ""],
    })
    assert r.status_code == 200, r.text
    upd = r.json()
    assert upd["question"] == "更新后的题干。"
    assert upd["status"] == "done"
    assert upd["tags"] == ["死锁", "银行家"]

    # 非法状态 / 非法难度 → 422
    assert client.patch(f"/api/wrongbook/{w['id']}", json={"status": "bogus"}).status_code == 422
    assert client.patch(f"/api/wrongbook/{w['id']}", json={"difficulty": "bogus"}).status_code == 422
    # 手动状态修正（重练之外的路径）
    assert client.patch(f"/api/wrongbook/{w['id']}", json={"status": "open"}).json()["status"] == "open"

    # 删除 → 404
    assert client.delete(f"/api/wrongbook/{w['id']}").status_code == 204
    assert client.get(f"/api/wrongbook/{w['id']}").status_code == 404
    assert client.delete(f"/api/wrongbook/{w['id']}").status_code == 404


def test_filters_and_pagination(client):
    """课程/章节/难度/状态/标签(any|all)/关键词过滤 + 过滤后分页计数。"""
    c1 = _course(client, "操作系统")
    c2 = _course(client, "数据库")
    w1 = _wrong(client, c1["id"], title="死锁判断题", chapter="第3章", difficulty="hard",
                tags=["死锁", "银行家"]).json()
    w2 = _wrong(client, c1["id"], title="内存管理计算题", chapter="第4章", difficulty="easy",
                tags=["内存管理"]).json()
    w3 = _wrong(client, c2["id"], title="SQL 事务隔离题", chapter="第5章", difficulty="medium",
                tags=["事务"]).json()

    def ids(**params):
        return [x["id"] for x in client.get("/api/wrongbook", params=params).json()]

    # 默认排序：待攻克在前、创建时间倒序（w3 最新）
    assert ids() == [w3["id"], w2["id"], w1["id"]]
    assert ids(course_id=c1["id"]) == [w2["id"], w1["id"]]
    assert ids(chapter="第4章") == [w2["id"]]
    assert ids(chapter="4章") == [w2["id"]]                      # 章节包含匹配
    assert ids(difficulty="hard") == [w1["id"]]
    assert client.get("/api/wrongbook", params={"status": "bogus"}).status_code == 422

    # 状态过滤：w1 改已掌握后只剩 open 的两条
    client.patch(f"/api/wrongbook/{w1['id']}", json={"status": "done"})
    assert ids(status="done") == [w1["id"]]
    assert ids(status="open") == [w3["id"], w2["id"]]

    # 标签 any / all（SQL 精确匹配）
    assert ids(tags=["死锁", "内存管理"], tag_mode="any") == [w2["id"], w1["id"]]
    assert ids(tags=["死锁", "银行家"], tag_mode="all") == [w1["id"]]
    assert ids(tags=["死锁", "银行家"], tag_mode="any") == [w1["id"]]

    # 关键词（标题/题干/答案/来源均可命中）
    assert ids(q="死锁判断题") == [w1["id"]]
    assert ids(q="隔离") == [w3["id"]]
    assert ids(q="2024") == [w3["id"], w2["id"], w1["id"]]   # 来源字段命中全部

    # 分页：过滤后总数在响应头，offset 不重不漏
    r = client.get("/api/wrongbook", params={"limit": 1, "offset": 0})
    assert r.headers["X-Total-Count"] == "3"
    assert r.headers["X-Has-More"] == "1"
    first = r.json()[0]["id"]
    r = client.get("/api/wrongbook", params={"limit": 1, "offset": 1})
    assert r.headers["X-Has-More"] == "1" and r.json()[0]["id"] != first
    r = client.get("/api/wrongbook", params={"limit": 10, "offset": 0, "course_id": c1["id"]})
    assert r.headers["X-Total-Count"] == "2"


def test_practice_mastery_flow(client):
    """重练自评：连续答对 2 次 → 已掌握；答错清零回退；队列只出待攻克。"""
    c = _course(client)
    w = _wrong(client, c["id"]).json()

    # 答对 1 次：streak=1，仍待攻克
    r = client.post(f"/api/wrongbook/{w['id']}/practice", json={"ok": True})
    s = r.json()
    assert s["streak"] == 1 and s["status"] == "open" and s["review_count"] == 1
    assert s["last_result"] == "correct" and s["last_reviewed_at"]

    # 答对 2 次：已掌握
    s = client.post(f"/api/wrongbook/{w['id']}/practice", json={"ok": True}).json()
    assert s["streak"] == 2 and s["status"] == "done"

    # 答错：streak 清零、回退待攻克、答错次数 +1
    s = client.post(f"/api/wrongbook/{w['id']}/practice", json={"ok": False}).json()
    assert s["streak"] == 0 and s["status"] == "open"
    assert s["wrong_count"] == 2 and s["review_count"] == 3

    # 队列：已掌握的不出现，待攻克的出现；限制条数；按课程过滤
    w2 = _wrong(client, c["id"], title="第二题").json()
    client.post(f"/api/wrongbook/{w2['id']}/practice", json={"ok": True})
    client.post(f"/api/wrongbook/{w2['id']}/practice", json={"ok": True})  # w2 → done
    queue = [x["id"] for x in client.get("/api/wrongbook/practice").json()]
    assert w["id"] in queue and w2["id"] not in queue
    assert len(client.get("/api/wrongbook/practice", params={"limit": 1}).json()) == 1

    # 404
    assert client.post("/api/wrongbook/9999/practice", json={"ok": True}).status_code == 404


def test_facets_and_stats(client):
    c1 = _course(client, "操作系统")
    c2 = _course(client, "数据库")
    _wrong(client, c1["id"], title="第2章题", chapter="第2章", tags=["死锁", "银行家"])
    _wrong(client, c1["id"], title="第10章题", chapter="第10章", tags=["死锁"])
    _wrong(client, c2["id"], title="第3章题", chapter="第3章", tags=["事务"])

    f = client.get("/api/wrongbook/facets").json()
    assert [x["chapter"] for x in f["chapters"]] == ["第2章", "第3章", "第10章"]  # 自然排序
    tags = {x["tag"]: x["count"] for x in f["tags"]}
    assert tags == {"死锁": 2, "银行家": 1, "事务": 1}

    s = client.get("/api/wrongbook/stats").json()
    assert s["total"] == 3 and s["open"] == 3 and s["done"] == 0
    assert s["mastered_pct"] == 0
    by_course = {(x["course_id"], x["open"], x["done"]) for x in s["by_course"]}
    assert (c1["id"], 2, 0) in by_course and (c2["id"], 1, 0) in by_course

    # 首页统计联动
    home = client.get("/api/stats").json()
    assert home["wrong_questions"] == 3 and home["wrong_questions_open"] == 3

    # 掌握后统计翻转
    wid = client.get("/api/wrongbook").json()[0]["id"]
    client.post(f"/api/wrongbook/{wid}/practice", json={"ok": True})
    client.post(f"/api/wrongbook/{wid}/practice", json={"ok": True})
    s = client.get("/api/wrongbook/stats").json()
    assert s["open"] == 2 and s["done"] == 1 and s["mastered_pct"] == 33


def test_md_export(client):
    c = _course(client)
    w = _wrong(client, c["id"], title="死锁错题", reason="漏算资源", tags=["死锁"]).json()

    r = client.get(f"/api/wrongbook/{w['id']}/md")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/markdown")
    assert "attachment" in r.headers["content-disposition"]
    body = r.text
    assert "# 死锁错题" in body
    for seg in ("题目", "我的答案", "正确答案与解析", "错因分析", "漏算资源", "标签：死锁"):
        assert seg in body

    r = client.get("/api/wrongbook/export/md")
    assert r.status_code == 200
    assert "共 1 题" in r.text and "待攻克 1" in r.text
    assert client.get("/api/wrongbook/9999/md").status_code == 404


def test_sync_roundtrip(client):
    """错题进同步快照：跨设备往返保留字段与课程引用；LWW 收敛；墓碑传播删除。"""
    c = _course(client)
    w = _wrong(client, c["id"], title="同步错题", chapter="第3章", difficulty="hard").json()
    client.post(f"/api/wrongbook/{w['id']}/practice", json={"ok": True})

    # 第二台设备（独立 SQLite），先预置一门课让 id 与 A 端错开，验证按 uuid 重映射
    from sqlalchemy import text

    path = settings.data_dir / f"wrongbook-device-b-{datetime.now().timestamp()}.db"
    eng = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    with eng.begin() as conn:
        conn.execute(text(
            "CREATE VIRTUAL TABLE IF NOT EXISTS materials_fts USING fts5("
            "title, text, tags, raw UNINDEXED, tokenize='unicode61')"
        ))
    DevB = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False)
    with DevB() as dbB:
        dbB.add(Course(name="B端已有课程", sync_uuid="pre-seeded-b-course"))
        dbB.commit()

    # A → B：课程 + 错题一并到达，课程引用按 uuid 重映射（A 课程 id=1 → B 本地 id=2）
    with SessionLocal() as dbA:
        snapA = build_snapshot(dbA)
    assert any(x["title"] == "同步错题" for x in snapA["wrong_questions"])
    with DevB() as dbB:
        stats = apply_snapshot(dbB, snapA)
        assert stats["created"] == 2  # A 的 1 课程 + 1 错题（B 预置课程不计）
        row = dbB.execute(select(WrongQuestion)).scalars().one()
        assert row.title == "同步错题"
        assert row.course_id == 2 and row.course_id != c["id"]  # 本地 id 重映射，非沿用 A 端 id
        assert row.chapter == "第3章" and row.difficulty == "hard"
        assert row.review_count == 1 and row.streak == 1
        assert row.status == "open"

    # B 端修改（状态改已掌握）→ 回传 A：LWW 收敛
    with DevB() as dbB:
        row = dbB.execute(select(WrongQuestion)).scalars().one()
        row.status = "done"
        dbB.commit()
        snapB = build_snapshot(dbB)
    with SessionLocal() as dbA:
        stats = apply_snapshot(dbA, snapB)
        assert stats["updated"] == 1
    got = client.get(f"/api/wrongbook/{w['id']}").json()
    assert got["status"] == "done"

    # 墓碑：A 端删除 → B 端同步后该题消失
    assert client.delete(f"/api/wrongbook/{w['id']}").status_code == 204
    with SessionLocal() as dbA:
        snapA2 = build_snapshot(dbA)
    with DevB() as dbB:
        apply_snapshot(dbB, snapA2)
        assert dbB.execute(select(WrongQuestion)).scalars().all() == []

    # 同步状态计数包含错题（A 端此刻为 0）
    counts = client.get("/api/sync/status").json()["counts"]
    assert counts["wrong_questions"] == 0


WRONG_TEXT = (
    "第3章 死锁（难点，考试重点）\n"
    "题目：某系统有 5 个进程和 3 类资源，按银行家算法判断系统是否安全。\n"
    "我的答案：安全。\n"
    "参考答案：不安全，存在死锁——未满足循环等待条件，可用资源分配矩阵判断。\n"
)


def test_refine_rules_channel(client):
    """无 LLM Key 时降级规则引擎：章节/难度/标签自动抽取，内容字段留空不编造。"""
    r = client.post("/api/wrongbook/refine", json={"text": WRONG_TEXT})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["engine"] == "rule"
    assert d["chapter"] == "第3章 死锁"
    assert d["difficulty"] == "hard"
    assert "死锁" in d["tags"]
    assert d["title"].startswith("第3章 死锁")
    assert d["question"] == WRONG_TEXT.strip()
    assert d["answer"] == "" and d["reason"] == ""  # 规则引擎绝不编造答案


def test_refine_validation(client):
    assert client.post("/api/wrongbook/refine", json={}).status_code == 422
    assert client.post("/api/wrongbook/refine", json={"text": "   "}).status_code == 422


def test_refine_llm_channel_and_fallback(client, monkeypatch):
    """LLM 可用走模型输出并做字段归一化；模型失败静默降级规则引擎。"""
    from app.infra.config import settings
    from app.domain.agent import wrongrefine as mod

    settings.llm_api_key = "test-key"
    mock = {
        "title": "银行家算法判断题",
        "question": "某系统有 5 个进程和 3 类资源，判断系统是否安全。",
        "my_answer": "安全",
        "answer": "不安全：存在死锁",
        "reason": "漏算循环等待条件",
        "chapter": "第3章 死锁",
        "difficulty": "bogus",  # 非法难度 → 归一化回落空串
        "tags": ["死锁", "银行家算法", "死锁"],  # 去重
    }
    monkeypatch.setattr(mod.llm, "chat_json", lambda *a, **k: mock)
    r = client.post("/api/wrongbook/refine", json={"text": WRONG_TEXT})
    d = r.json()
    assert r.status_code == 200, r.text
    assert d["engine"] == "llm"
    assert d["title"] == "银行家算法判断题"
    assert d["my_answer"] == "安全"
    assert d["difficulty"] == ""
    assert d["tags"] == ["死锁", "银行家算法"]

    # LLM 调用失败 → 降级规则引擎，仍然可用
    monkeypatch.setattr(mod.llm, "chat_json", lambda *a, **k: None)
    d = client.post("/api/wrongbook/refine", json={"text": WRONG_TEXT}).json()
    assert d["engine"] == "rule"
    assert d["chapter"] == "第3章 死锁"

    # LLM 返回无实质内容 → 规则兜底
    monkeypatch.setattr(mod.llm, "chat_json", lambda *a, **k: {"title": "", "question": ""})
    d = client.post("/api/wrongbook/refine", json={"text": WRONG_TEXT}).json()
    assert d["engine"] == "rule"
