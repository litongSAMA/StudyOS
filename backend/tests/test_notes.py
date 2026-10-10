"""AI 笔记提炼：离线规则提炼、考点优先、资料全文提炼、LLM 双通道降级、
笔记 CRUD 与 Markdown 导出、多端同步往返。"""
from __future__ import annotations


def _material(client, text=None, title="死锁课件"):
    c = client.post("/api/courses", json={"name": "操作系统"}).json()
    m = client.post("/api/materials", json={
        "course_id": c["id"], "title": title, "type": "courseware",
        "text": text or "死锁产生的四个必要条件与银行家算法。",
    }).json()
    return m


def _refine(client, body):
    return client.post("/api/notes/refine", json=body)


DEADLOCK_TEXT = (
    "死锁是指多个进程因竞争资源而相互等待，无法继续推进的现象。"
    "产生死锁必须同时满足四个必要条件：互斥条件、请求与保持条件、不可剥夺条件、循环等待条件。"
    "破坏其中任意一个条件即可预防死锁。"
    "银行家算法是一种避免死锁的经典算法，核心思想是在分配资源前判断系统是否处于安全状态。"
    "考试重点：银行家算法的安全性检测步骤是必考内容，常考计算题。"
    "死锁检测通过资源分配图化简实现。"
)


def test_refine_offline_rule_engine(client):
    r = _refine(client, {"text": DEADLOCK_TEXT, "mode": "keypoints"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["engine"] == "rule"  # 离线：LLM 未配置 → 规则引擎
    assert data["mode"] == "keypoints"
    assert data["summary"]
    assert data["points"], "离线规则也应产出要点"
    assert all(p["point"] and p["importance"] in ("high", "medium", "low") for p in data["points"])
    # 概要取自原文句（抽取式，不编造）
    for p in data["points"]:
        assert p["point"] in DEADLOCK_TEXT


def test_refine_exam_mode_prioritizes_exam_sentences(client):
    """考点模式：含「考试重点/必考」的句子必须入选且标记 high。"""
    r = _refine(client, {"text": DEADLOCK_TEXT, "mode": "exam"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["mode"] == "exam"
    points = data["points"]
    exam_sents = [p for p in points if "必考" in p["point"] or "考试重点" in p["point"]]
    assert exam_sents, "考点句应出现在考点笔记要点中"
    assert all(p["importance"] == "high" for p in exam_sents)


def test_refine_from_material_uses_full_text(client):
    m = _material(client, text=DEADLOCK_TEXT, title="第3章 死锁（考试重点）")
    r = _refine(client, {"material_id": m["id"], "mode": "exam"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["title"] == m["title"]  # title_hint 来自资料标题
    assert data["points"], "应从资料全文提炼出要点"
    assert any("银行家" in p["point"] for p in data["points"])

    # 资料不存在 → 404
    assert _refine(client, {"material_id": 9999}).status_code == 404


def test_refine_validation(client):
    assert _refine(client, {}).status_code == 422
    assert _refine(client, {"text": "   "}).status_code == 422
    assert _refine(client, {"text": "内容", "mode": "bogus"}).status_code == 422


def test_refine_llm_channel_and_fallback(client, monkeypatch):
    """LLM 可用时走模型输出；模型失败时静默降级离线规则（演示不崩）。"""
    from app.infra.config import settings
    from app.domain.agent import refine as refine_mod

    settings.llm_api_key = "test-key"

    mock = {
        "title": "死锁与银行家算法",
        "summary": "AI 生成的概要。",
        "points": [
            {"point": "AI 提炼：四个必要条件", "importance": "high"},
            {"point": "AI 提炼：银行家算法必考", "importance": "high"},
        ],
    }
    monkeypatch.setattr(refine_mod.llm, "chat_json", lambda *a, **k: mock)
    r = _refine(client, {"text": DEADLOCK_TEXT, "mode": "exam"})
    data = r.json()
    assert data["engine"] == "llm"
    assert data["title"] == "死锁与银行家算法"
    assert [p["point"] for p in data["points"]] == ["AI 提炼：四个必要条件", "AI 提炼：银行家算法必考"]

    # LLM 调用失败 → 降级规则引擎，仍然可用
    monkeypatch.setattr(refine_mod.llm, "chat_json", lambda *a, **k: None)
    r = _refine(client, {"text": DEADLOCK_TEXT, "mode": "exam"})
    data = r.json()
    assert data["engine"] == "rule"
    assert data["points"]


def test_note_crud(client):
    m = _material(client, text=DEADLOCK_TEXT)
    r = client.post("/api/notes", json={
        "material_id": m["id"], "title": "死锁重点笔记", "mode": "keypoints",
        "summary": "死锁四个必要条件与预防。",
        "points": [
            {"point": "四个必要条件：互斥、请求保持、不可剥夺、循环等待", "importance": "high"},
            {"point": "破坏任一条件即可预防", "importance": "medium"},
        ],
        "engine": "rule",
    })
    assert r.status_code == 201, r.text
    note = r.json()
    assert note["material_title"] == m["title"]
    assert note["course_name"] == "操作系统"
    assert len(note["points"]) == 2

    # 列表 + 按资料过滤
    assert client.get("/api/notes").json()[0]["id"] == note["id"]
    assert [x["id"] for x in client.get("/api/notes", params={"material_id": m["id"]}).json()] == [note["id"]]
    assert client.get("/api/notes", params={"material_id": 9999}).json() == []

    # 单条读取
    assert client.get(f"/api/notes/{note['id']}").json()["title"] == "死锁重点笔记"

    # 编辑：改标题/要点/模式；非法要点被归一化过滤
    r = client.patch(f"/api/notes/{note['id']}", json={
        "title": "死锁考点笔记", "mode": "exam",
        "points": [
            {"point": "银行家算法安全性检测必考", "importance": "high"},
            {"point": "", "importance": "high"},          # 空要点 → 过滤
            {"point": "资源分配图化简", "importance": "weird"},  # 非法 importance → medium
        ],
    })
    assert r.status_code == 200, r.text
    upd = r.json()
    assert upd["title"] == "死锁考点笔记" and upd["mode"] == "exam"
    assert [p["point"] for p in upd["points"]] == ["银行家算法安全性检测必考", "资源分配图化简"]
    assert upd["points"][1]["importance"] == "medium"

    # 非法模式 / 不存在的资料 → 422 / 404
    assert client.post("/api/notes", json={"title": "x", "mode": "bogus"}).status_code == 422
    assert client.post("/api/notes", json={"title": "x", "material_id": 9999}).status_code == 404
    assert client.patch(f"/api/notes/{note['id']}", json={"mode": "bogus"}).status_code == 422

    # 删除 → 404
    assert client.delete(f"/api/notes/{note['id']}").status_code == 204
    assert client.get(f"/api/notes/{note['id']}").status_code == 404
    assert client.delete(f"/api/notes/{note['id']}").status_code == 404


def test_note_md_export(client):
    note = client.post("/api/notes", json={
        "title": "死锁重点笔记", "mode": "keypoints",
        "summary": "死锁四个必要条件。",
        "points": [{"point": "互斥条件", "importance": "high"}],
        "engine": "rule",
    }).json()

    r = client.get(f"/api/notes/{note['id']}/md")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/markdown")
    assert "attachment" in r.headers["content-disposition"]
    body = r.text
    assert "# 死锁重点笔记" in body and "死锁四个必要条件" in body
    assert "★" in body and "互斥条件" in body

    # 全部导出
    r = client.get("/api/notes/export/md")
    assert r.status_code == 200
    assert "死锁重点笔记" in r.text

    # 不存在的笔记 → 404
    assert client.get("/api/notes/9999/md").status_code == 404
