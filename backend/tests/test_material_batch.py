"""资料批量操作：批量删除 / 改标签 / 换课程 / 改归档字段。

回归背景：前端资料页（`web/src/pages/Materials.tsx`）一直调用
`POST /api/materials/batch/{delete,tags,course,fields}`，但后端曾缺失这四条路由，
批量整理在实际运行中会 404（"没反应"）。本文件按前端契约逐条锁住行为：
- 结果结构 `{updated, deleted, missing}` 与前端 `MaterialBatchResult` 一致；
- 未知 id 计入 `missing` 而不是报错（并发删除/多端同步后重试要幂等）；
- 输入校验（空列表 / 超上限 / 非法难度 / 非法的空字段提交）明确 422；
- 标签 add/remove/set 三种模式与去重、清空语义；
- 删除要连带清理附件、全文索引、语义缓存与间隔重复状态。
"""
from __future__ import annotations

import pytest

from app.infra.db import SessionLocal
from app.infra.models import Material, MaterialReview
from sqlalchemy import select, text


def _mat(client, title, *, tags=None, type="notes", course_id=None, text_="正文"):
    body = {"title": title, "type": type, "text": text_, "tags": tags or []}
    if course_id is not None:
        body["course_id"] = course_id
    r = client.post("/api/materials", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _course(client, name="操作系统"):
    r = client.post("/api/courses", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()


def _ids(rows):
    return [m["id"] for m in rows]


# ---------------- 批量删除 ----------------

def test_batch_delete_removes_rows_and_reports_missing(client):
    a, b = _mat(client, "A"), _mat(client, "B")
    r = client.post("/api/materials/batch/delete", json={"ids": [a["id"], b["id"]]})
    assert r.status_code == 200, r.text
    assert r.json() == {"updated": 0, "deleted": 2, "missing": 0}
    assert client.get("/api/materials").json() == []


def test_batch_delete_unknown_ids_count_as_missing_not_error(client):
    """并发删除 / 多端同步后重试：未知 id 不报错，只计入 missing（幂等重试安全）。"""
    a = _mat(client, "A")
    r = client.post("/api/materials/batch/delete", json={"ids": [a["id"], 999999]})
    assert r.status_code == 200, r.text
    assert r.json() == {"updated": 0, "deleted": 1, "missing": 1}


def test_batch_delete_clears_fts_semantic_and_review_state(client):
    """删除资料要连带清掉全文索引、语义缓存与间隔重复状态，不留孤儿数据。"""
    m = _mat(client, "傅里叶变换讲义", tags=["信号"])
    assert client.post("/api/review/rate", json={"material_id": m["id"], "rating": "good"}).status_code == 200
    assert client.post("/api/search/semantic/rebuild").status_code == 200

    assert client.post("/api/materials/batch/delete", json={"ids": [m["id"]]}).json()["deleted"] == 1

    # 全文索引：删掉的资料不能再被检索到
    assert client.get("/api/search", params={"q": "傅里叶"}).json()["total"] == 0
    with SessionLocal() as db:
        assert db.execute(select(Material)).scalars().all() == []
        assert db.execute(select(MaterialReview)).scalars().all() == []
        assert db.execute(text("SELECT count(*) FROM materials_fts")).scalar() == 0
        assert db.execute(text("SELECT count(*) FROM semantic_vectors")).scalar() == 0


def test_batch_delete_deduplicates_repeated_ids(client):
    """重复 id 只算一次：`deleted` 不能大于实际行数（前端用它做提示文案）。"""
    a = _mat(client, "A")
    r = client.post("/api/materials/batch/delete", json={"ids": [a["id"], a["id"]]})
    assert r.json() == {"updated": 0, "deleted": 1, "missing": 0}


def test_batch_delete_empty_ids_422(client):
    r = client.post("/api/materials/batch/delete", json={"ids": []})
    assert r.status_code == 422


def test_batch_delete_rejects_oversized_batch(client):
    r = client.post("/api/materials/batch/delete", json={"ids": list(range(1, 502))})
    assert r.status_code == 422
    assert "500" in r.text


# ---------------- 批量改标签 ----------------

def test_batch_tags_add_and_dedupe(client):
    a, b = _mat(client, "A", tags=["信号"]), _mat(client, "B", tags=[])
    r = client.post(
        "/api/materials/batch/tags",
        json={"ids": [a["id"], b["id"]], "tags": ["考点", " 考点 ", ""], "mode": "add"},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"updated": 2, "deleted": 0, "missing": 0}
    got = {m["title"]: m["tags"] for m in client.get("/api/materials").json()}
    assert got["A"] == ["信号", "考点"]      # 追加且不重复
    assert got["B"] == ["考点"]              # 空标签与重复标签被过滤


def test_batch_tags_add_is_idempotent(client):
    a = _mat(client, "A", tags=["信号"])
    for _ in range(2):
        assert client.post(
            "/api/materials/batch/tags",
            json={"ids": [a["id"]], "tags": ["信号"], "mode": "add"},
        ).status_code == 200
    assert client.get("/api/materials").json()[0]["tags"] == ["信号"]


def test_batch_tags_remove_and_set_clears(client):
    a = _mat(client, "A", tags=["信号", "考点"])
    assert client.post(
        "/api/materials/batch/tags",
        json={"ids": [a["id"]], "tags": ["考点"], "mode": "remove"},
    ).status_code == 200
    assert client.get("/api/materials").json()[0]["tags"] == ["信号"]
    # set 空数组 = 清空标签
    assert client.post(
        "/api/materials/batch/tags",
        json={"ids": [a["id"]], "tags": [], "mode": "set"},
    ).status_code == 200
    assert client.get("/api/materials").json()[0]["tags"] == []


def test_batch_tags_invalid_mode_422(client):
    a = _mat(client, "A")
    r = client.post(
        "/api/materials/batch/tags",
        json={"ids": [a["id"]], "tags": ["x"], "mode": "append"},
    )
    assert r.status_code == 422


def test_batch_tags_keeps_full_text_index_in_sync(client):
    """批量改标签会重写资料行 → 全文索引必须同步更新，检索结果不能因此丢失。"""
    a = _mat(client, "银行家算法实验报告", tags=[])
    assert client.post(
        "/api/materials/batch/tags", json={"ids": [a["id"]], "tags": ["死锁"], "mode": "add"}
    ).status_code == 200
    hits = client.get("/api/search", params={"q": "银行家"}).json()
    assert [h["title"] for h in hits["materials"]] == ["银行家算法实验报告"]
    assert hits["materials"][0]["tags"] == ["死锁"]


# ---------------- 批量换课程 ----------------

def test_batch_course_moves_and_unclassifies(client):
    course = _course(client)
    a, b = _mat(client, "A"), _mat(client, "B")
    r = client.post(
        "/api/materials/batch/course", json={"ids": [a["id"], b["id"]], "course_id": course["id"]}
    )
    assert r.json()["updated"] == 2
    assert all(m["course_id"] == course["id"] for m in client.get("/api/materials").json())

    # course_id=null → 移到「未分类」
    assert client.post(
        "/api/materials/batch/course", json={"ids": [a["id"]], "course_id": None}
    ).status_code == 200
    got = {m["title"]: m["course_id"] for m in client.get("/api/materials").json()}
    assert got == {"A": None, "B": course["id"]}


def test_batch_course_unknown_course_404(client):
    a = _mat(client, "A")
    r = client.post("/api/materials/batch/course", json={"ids": [a["id"]], "course_id": 999999})
    assert r.status_code == 404


# ---------------- 批量改归档字段 ----------------

def test_batch_fields_only_touches_submitted_keys(client):
    a, b = _mat(client, "A"), _mat(client, "B")
    r = client.post(
        "/api/materials/batch/fields",
        json={"ids": [a["id"], b["id"]], "chapter": "第3章 内存管理", "exam_focus": True},
    )
    assert r.status_code == 200, r.text
    assert r.json()["updated"] == 2
    for m in client.get("/api/materials").json():
        assert m["chapter"] == "第3章 内存管理"
        assert m["exam_focus"] is True
        assert m["difficulty"] == ""       # 未提交的字段保持原样
        assert m["type"] == "notes"


def test_batch_fields_empty_string_clears(client):
    a = _mat(client, "A")
    client.post("/api/materials/batch/fields", json={"ids": [a["id"]], "chapter": "第1章"})
    assert client.post(
        "/api/materials/batch/fields",
        json={"ids": [a["id"]], "chapter": "", "difficulty": ""},
    ).status_code == 200
    m = client.get("/api/materials").json()[0]
    assert m["chapter"] == "" and m["difficulty"] == ""


def test_batch_fields_rejects_invalid_difficulty(client):
    a = _mat(client, "A")
    r = client.post("/api/materials/batch/fields", json={"ids": [a["id"]], "difficulty": "超难"})
    assert r.status_code == 422


def test_batch_fields_requires_at_least_one_field(client):
    a = _mat(client, "A")
    r = client.post("/api/materials/batch/fields", json={"ids": [a["id"]]})
    assert r.status_code == 422


def test_batch_fields_filters_by_new_values(client):
    """批量改完归档字段后，过滤必须按新值命中（过滤走 SQL 下推，不能是内存筛）。"""
    a = _mat(client, "A")
    _mat(client, "B")
    client.post(
        "/api/materials/batch/fields",
        json={"ids": [a["id"]], "chapter": "第7章 图论", "difficulty": "hard", "exam_focus": True},
    )
    r = client.get("/api/materials", params={"chapter": "图论", "difficulty": "hard", "exam_focus": True})
    assert [m["title"] for m in r.json()] == ["A"]
    assert r.headers["X-Total-Count"] == "1"
