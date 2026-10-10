"""资料过滤：标签过滤下推 SQL（不再「先 limit 后内存过滤」）、多标签、分页、标签聚合。

回归重点：命中标签的资料位于「最旧」一端时，小 limit 也必须能取到它——
这正是旧实现（先 LIMIT 一页、再在 Python 里筛标签）会静默丢结果的场景。
"""
from __future__ import annotations

import pytest


def _mat(client, title, *, tags, type="notes", course_id=None, text="正文"):
    body = {"title": title, "type": type, "text": text, "tags": tags}
    if course_id is not None:
        body["course_id"] = course_id
    r = client.post("/api/materials", json=body)
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture
def haystack(client):
    """1 条目标资料（最早创建）+ 24 条噪声资料（更晚创建，默认排在前面）。"""
    target = _mat(client, "傅里叶变换讲义", tags=["信号", "考点"], type="handout")
    for i in range(24):
        _mat(client, f"噪声资料{i}", tags=["常见"])
    return target


def test_tag_filter_not_truncated_by_limit(client, haystack):
    """标签过滤在 SQL 内完成 → limit 作用于「过滤之后」，结果完整且总数可信。"""
    r = client.get("/api/materials", params={"tag": "考点", "limit": 5})
    assert r.status_code == 200
    items = r.json()
    assert [m["title"] for m in items] == ["傅里叶变换讲义"]
    assert r.headers["X-Total-Count"] == "1"
    assert r.headers["X-Has-More"] == "0"
    assert r.headers["X-Tag-Filter"] == "sql"

    # 未过滤时 limit 正常生效，且总数是全库条数
    r = client.get("/api/materials", params={"limit": 5})
    assert len(r.json()) == 5
    assert r.headers["X-Total-Count"] == "25"
    assert r.headers["X-Has-More"] == "1"


def test_tag_filter_in_search_not_truncated(client, haystack):
    """检索链路同样下推：空查询 + 标签、关键词 + 标签都不受 limit 截断影响。"""
    body = client.get("/api/search", params={"q": "", "tag": "考点", "limit": 3}).json()
    assert body["total"] == 1
    assert body["materials"][0]["title"] == "傅里叶变换讲义"

    body = client.get("/api/search", params={"q": "傅里叶", "tag": "考点"}).json()
    assert body["total"] == 1
    # 标签不匹配 → 关键词命中也应被过滤掉
    assert client.get("/api/search", params={"q": "傅里叶", "tag": "常见"}).json()["total"] == 0


def test_tag_filter_is_exact_not_substring(client):
    """标签匹配必须是数组元素精确相等，不能退化成 JSON 文本子串匹配。"""
    _mat(client, "死锁分析", tags=["死锁分析"])
    _mat(client, "死锁课件", tags=["死锁"])
    got = client.get("/api/materials", params={"tag": "死锁"}).json()
    assert [m["title"] for m in got] == ["死锁课件"]


def test_multi_tag_any_and_all(client):
    _mat(client, "A", tags=["进程", "调度"])
    _mat(client, "B", tags=["进程"])
    _mat(client, "C", tags=["信号"])

    any_titles = {
        m["title"]
        for m in client.get("/api/materials", params={"tags": ["进程", "信号"]}).json()
    }
    assert any_titles == {"A", "B", "C"}

    all_titles = [
        m["title"]
        for m in client.get(
            "/api/materials", params={"tags": "进程,调度", "tag_mode": "all"}
        ).json()
    ]
    assert all_titles == ["A"]

    # 检索链路同样支持多标签
    body = client.get(
        "/api/search", params={"q": "", "tags": ["进程", "调度"], "tag_mode": "all"}
    ).json()
    assert [m["title"] for m in body["materials"]] == ["A"]


def test_pagination_offset_is_stable(client, haystack):
    first = client.get("/api/materials", params={"limit": 10, "offset": 0})
    second = client.get("/api/materials", params={"limit": 10, "offset": 10})
    third = client.get("/api/materials", params={"limit": 10, "offset": 20})
    ids = [m["id"] for m in first.json()] + [m["id"] for m in second.json()]
    ids += [m["id"] for m in third.json()]
    assert len(ids) == 25            # 不重不漏
    assert len(set(ids)) == 25
    assert second.headers["X-Offset"] == "10"
    assert third.headers["X-Has-More"] == "0"


def test_tag_facets_cover_whole_library(client, haystack):
    """标签云走 SQL GROUP BY：统计全库标签，而非当前一页里出现的标签。"""
    facets = client.get("/api/materials/tags").json()
    counts = {f["tag"]: f["count"] for f in facets}
    assert counts["常见"] == 24
    assert counts["考点"] == 1 and counts["信号"] == 1
    # 热度倒序
    assert facets[0]["tag"] == "常见"

    # 类型过滤对聚合生效
    facets = client.get("/api/materials/tags", params={"type": "handout"}).json()
    assert {f["tag"] for f in facets} == {"信号", "考点"}


def test_tag_facets_respect_course(client):
    c1 = client.post("/api/courses", json={"name": "操作系统"}).json()
    c2 = client.post("/api/courses", json={"name": "信号与系统"}).json()
    _mat(client, "OS 课件", tags=["死锁"], course_id=c1["id"])
    _mat(client, "信号课件", tags=["频域"], course_id=c2["id"])
    facets = client.get("/api/materials/tags", params={"course_id": c1["id"]}).json()
    assert [f["tag"] for f in facets] == ["死锁"]


def test_tag_filter_memory_fallback_matches_sql(client, haystack, monkeypatch):
    """极端环境（SQLite 无 JSON1）走内存兜底：结果必须与 SQL 路径一致，且仍不被截断。"""
    monkeypatch.setattr("app.infra.materialquery._json1_ready", False)
    r = client.get("/api/materials", params={"tag": "考点", "limit": 5})
    assert [m["title"] for m in r.json()] == ["傅里叶变换讲义"]
    assert r.headers["X-Total-Count"] == "1"
    assert r.headers["X-Tag-Filter"] == "memory"   # 兜底路径可观测
    facets = client.get("/api/materials/tags").json()
    assert {f["tag"]: f["count"] for f in facets}["常见"] == 24
    body = client.get("/api/search", params={"q": "傅里叶", "tag": "考点"}).json()
    assert body["total"] == 1


def test_limit_is_clamped(client, haystack):
    """limit 越界不报错：钳制在 1..1000。"""
    assert len(client.get("/api/materials", params={"limit": 0}).json()) == 1
    assert len(client.get("/api/materials", params={"limit": 99999}).json()) == 25


def test_bad_tag_mode_rejected(client):
    assert client.get("/api/materials", params={"tag_mode": "weird"}).status_code == 422
