"""清单 / 模版库 / 分享链接：清单 CRUD 与归一化、内置模版幂等播种与保护、
用户模版 CRUD、清单存为模版、分享创建/公开查看/过期/撤销/查看计数、
一键导入（payload 与 url 双通道）、Markdown 导出。"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from app.domain.checklistlib import BUILTIN_TEMPLATES, seed_builtin_templates
from app.infra.db import SessionLocal
from app.infra.models import ChecklistShare, ChecklistTemplate
from app.infra.timeutil import utcnow


def _seed_templates():
    """每个用例独立清库，内置模版按需显式播种（与 init_db 同一条路径）。"""
    with SessionLocal() as db:
        seed_builtin_templates(db)


def _course(client, name="操作系统"):
    return client.post("/api/courses", json={"name": name}).json()


def _checklist(client, title="期末复习清单", **kw):
    body = {
        "title": title,
        "items": [
            {"text": "整理错题", "done": False, "note": ""},
            {"text": "做一套真题", "done": True, "note": "计时"},
        ],
    }
    body.update(kw)
    r = client.post("/api/checklists", json=body)
    assert r.status_code == 201, r.text
    return r.json()


# ---------------------------------------------------------------------------
# 清单 CRUD 与归一化
# ---------------------------------------------------------------------------

def test_checklist_crud_and_progress(client):
    course = _course(client)
    c = _checklist(client, course_id=course["id"], tags=["期末", " 期末 "], description=" 冲刺 ")
    assert c["course_id"] == course["id"]
    assert c["course_name"] == course["name"]
    assert c["tags"] == ["期末"]  # 去重去空白
    assert c["description"] == "冲刺"
    assert c["total_count"] == 2
    assert c["done_count"] == 1

    # 列表与单条
    assert client.get("/api/checklists").json()[0]["id"] == c["id"]
    assert client.get(f"/api/checklists/{c['id']}").json()["title"] == "期末复习清单"

    # 状态过滤：done（全部勾完）/ open（未勾完）
    assert [x["id"] for x in client.get("/api/checklists", params={"status": "done"}).json()] == []
    assert [x["id"] for x in client.get("/api/checklists", params={"status": "open"}).json()] == [c["id"]]

    # PATCH：改标题/勾选/清空课程
    r = client.patch(f"/api/checklists/{c['id']}", json={
        "title": "  期末冲刺清单  ",
        "course_id": None,
        "items": [{"text": "整理错题", "done": True}, {"text": "过公式表"}],
    })
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["title"] == "期末冲刺清单"
    assert d["course_id"] is None
    assert d["done_count"] == 1 and d["total_count"] == 2

    # 全部勾完 → done
    client.patch(f"/api/checklists/{c['id']}", json={
        "items": [{"text": "a", "done": True}],
    })
    done = client.get("/api/checklists", params={"status": "done"}).json()
    assert [x["id"] for x in done] == [c["id"]]

    # 删除后 404
    assert client.delete(f"/api/checklists/{c['id']}").status_code == 204
    assert client.get(f"/api/checklists/{c['id']}").status_code == 404


def test_checklist_validation(client):
    # 空标题 422
    assert client.post("/api/checklists", json={"title": "  "}).status_code == 422
    # 课程不存在 404
    r = client.post("/api/checklists", json={"title": "x", "course_id": 9999})
    assert r.status_code == 404
    # 清单项归一化：空文本剔除、note 截断
    c = _checklist(client, items=[
        {"text": "", "done": True},  # 空项剔除
        {"text": "  ", "note": "x"},
        {"text": "有效项", "done": True, "note": "y" * 400},
    ])
    assert c["total_count"] == 1
    assert c["items"][0]["text"] == "有效项"
    assert len(c["items"][0]["note"]) == 300
    # 不存在的清单 404（GET/PATCH/DELETE）
    assert client.get("/api/checklists/9999").status_code == 404
    assert client.patch("/api/checklists/9999", json={"title": "x"}).status_code == 404
    assert client.delete("/api/checklists/9999").status_code == 404
    # PATCH 标题清空 → 422
    assert client.patch(f"/api/checklists/{c['id']}", json={"title": " "}).status_code == 422


def test_checklist_md_export(client):
    course = _course(client)
    c = _checklist(client, course_id=course["id"], tags=["期末"], description="冲刺两周")
    r = client.get(f"/api/checklists/{c['id']}/md")
    assert r.status_code == 200
    body = r.text
    assert "# 期末复习清单" in body
    assert "- [ ] 整理错题" in body
    assert "- [x] 做一套真题（计时）" in body
    assert "课程：操作系统" in body
    assert "标签：期末" in body
    assert "冲刺两周" in body
    assert r.headers["content-disposition"].startswith("attachment")
    assert client.get("/api/checklists/9999/md").status_code == 404


# ---------------------------------------------------------------------------
# 模版库：内置 + 用户自存
# ---------------------------------------------------------------------------

def test_builtin_templates_seeded_and_upgrade(client):
    _seed_templates()
    r = client.get("/api/checklist-templates")
    assert r.status_code == 200
    data = r.json()
    builtins = [t for t in data if t["builtin"]]
    assert len(builtins) == len(BUILTIN_TEMPLATES), "内置模版应全部播种"
    keys = [t["key"] for t in builtins]
    assert len(keys) == len(set(keys)) and all(keys)
    assert all(t["items"] for t in builtins), "每个内置模版都应有清单项"
    assert all(not i["done"] for t in builtins for i in t["items"])
    # 分类合法 + 分类接口
    cats = set(client.get("/api/checklist-templates/categories").json())
    assert all(t["category"] in cats for t in builtins)

    # 升级路径：手改内置模版 → 再次播种恢复官方内容（幂等 upsert）
    with SessionLocal() as db:
        t = db.execute(
            select(ChecklistTemplate).where(ChecklistTemplate.key == "cet30")
        ).scalar_one()
        t.title = "被改过的标题"
        db.commit()
    with SessionLocal() as db:
        seed_builtin_templates(db)
    cet = next(
        t for t in client.get("/api/checklist-templates").json() if t["key"] == "cet30"
    )
    assert cet["title"] == "四六级备考清单"

    # 重复播种不产生重复模版
    _seed_templates()
    again = client.get("/api/checklist-templates").json()
    assert len([t for t in again if t["builtin"]]) == len(BUILTIN_TEMPLATES)

    # 分类过滤
    by_cat = client.get(
        "/api/checklist-templates", params={"category": "英语考试"}
    ).json()
    assert by_cat and all(t["category"] == "英语考试" for t in by_cat)


def test_user_template_crud_and_builtin_protection(client):
    _seed_templates()
    # 创建用户模版（done 一律清零）
    r = client.post("/api/checklist-templates", json={
        "title": "我的周计划",
        "category": "日常学习",
        "description": "每周例行",
        "tags": ["每周", "每周"],
        "items": [{"text": "复盘", "done": True}],
    })
    assert r.status_code == 201, r.text
    t = r.json()
    assert t["builtin"] is False and t["key"] is None
    assert t["items"][0]["done"] is False
    assert t["tags"] == ["每周"]
    assert client.get(f"/api/checklist-templates/{t['id']}").status_code == 200

    # PATCH 用户模版
    r = client.patch(f"/api/checklist-templates/{t['id']}", json={
        "title": "我的双周计划", "category": "日常学习",
    })
    assert r.status_code == 200
    assert r.json()["title"] == "我的双周计划"
    # 非法分类 422
    assert client.patch(
        f"/api/checklist-templates/{t['id']}", json={"category": "不存在分类"}
    ).status_code == 422
    assert client.post("/api/checklist-templates", json={
        "title": "x", "category": "不存在分类",
    }).status_code == 422
    # 删除用户模版
    assert client.delete(f"/api/checklist-templates/{t['id']}").status_code == 204
    assert client.get(f"/api/checklist-templates/{t['id']}").status_code == 404

    # 内置模版受保护：PATCH/DELETE → 403
    builtin = next(
        t for t in client.get("/api/checklist-templates").json() if t["builtin"]
    )
    assert client.patch(
        f"/api/checklist-templates/{builtin['id']}", json={"title": "x"}
    ).status_code == 403
    assert client.delete(f"/api/checklist-templates/{builtin['id']}").status_code == 403


def test_save_checklist_as_template(client):
    course = _course(client)
    c = _checklist(client, course_id=course["id"], tags=["期末"], description="冲刺")
    r = client.post(f"/api/checklists/{c['id']}/template", json={
        "title": "我的冲刺模版", "category": "复习备考",
    })
    assert r.status_code == 201, r.text
    t = r.json()
    assert t["title"] == "我的冲刺模版"
    assert t["category"] == "复习备考"
    assert t["source_checklist_id"] == c["id"]
    assert t["builtin"] is False
    # 项内容保留、勾选清零
    assert [i["text"] for i in t["items"]] == ["整理错题", "做一套真题"]
    assert all(not i["done"] for i in t["items"])
    # 缺省标题用清单标题、缺省分类回落「日常学习」
    r2 = client.post(f"/api/checklists/{c['id']}/template", json={})
    assert r2.status_code == 201
    assert r2.json()["title"] == "期末复习清单"
    assert r2.json()["category"] == "日常学习"
    # 清单不存在 404；分类非法 422
    assert client.post("/api/checklists/9999/template", json={}).status_code == 404
    assert client.post(
        f"/api/checklists/{c['id']}/template", json={"category": "bad"}
    ).status_code == 422


# ---------------------------------------------------------------------------
# 分享链接
# ---------------------------------------------------------------------------

def test_share_create_and_public_view(client, lan_client):
    course = _course(client)
    c = _checklist(client, course_id=course["id"], tags=["期末"])
    r = client.post(f"/api/checklists/{c['id']}/share", json={})
    assert r.status_code == 201, r.text
    s = r.json()
    assert s["checklist_id"] == c["id"]
    assert s["checklist_title"] == "期末复习清单"
    assert s["expires_at"] is None
    assert s["url"].endswith(f"/#/s/{s['token']}")
    assert s["api_url"].endswith(f"/api/share/{s['token']}")

    # 本机（loopback）查看 → is_owner=True
    v = client.get(f"/api/share/{s['token']}").json()
    assert v["is_owner"] is True
    assert v["title"] == "期末复习清单"
    assert v["course_name"] == course["name"]
    assert [i["text"] for i in v["items"]] == ["整理错题", "做一套真题"]
    assert v["items"][1]["done"] is True and v["items"][1]["note"] == "计时"
    # 局域网其它设备（非 loopback）→ is_owner=False，且是公开 GET（无需同源）
    v2 = lan_client.get(f"/api/share/{s['token']}")
    assert v2.status_code == 200, v2.text
    assert v2.json()["is_owner"] is False
    assert v2.headers.get("access-control-allow-origin") == "*"
    # 查看计数累加
    client.get(f"/api/share/{s['token']}")
    info = client.get(f"/api/checklists/{c['id']}/shares").json()
    assert info[0]["view_count"] >= 3
    # 全量分享列表
    all_shares = client.get("/api/shares").json()
    assert [x["token"] for x in all_shares] == [s["token"]]


def test_share_expiry_and_revoke(client, lan_client):
    c = _checklist(client)
    s = client.post(f"/api/checklists/{c['id']}/share", json={"expires_h": 24}).json()
    assert s["expires_at"] is not None
    assert client.get(f"/api/share/{s['token']}").status_code == 200
    # 把过期时间拨回过去 → 410
    with SessionLocal() as db:
        row = db.get(ChecklistShare, s["token"])
        row.expires_at = utcnow() - timedelta(hours=1)
        db.commit()
    assert lan_client.get(f"/api/share/{s['token']}").status_code == 410

    # 撤销 → 404；重复撤销幂等 OK；不存在 404
    s2 = client.post(f"/api/checklists/{c['id']}/share", json={}).json()
    assert client.delete(f"/api/shares/{s2['token']}").json()["ok"] is True
    assert lan_client.get(f"/api/share/{s2['token']}").status_code == 404
    assert client.delete(f"/api/shares/{s2['token']}").status_code == 200
    assert client.delete("/api/shares/not-exist").status_code == 404


def test_share_invalid_params_and_missing_checklist(client):
    c = _checklist(client)
    for bad in (-1, 0, 8761, 100000):
        assert client.post(
            f"/api/checklists/{c['id']}/share", json={"expires_h": bad}
        ).status_code == 422
    assert client.post("/api/checklists/9999/share", json={}).status_code == 404
    assert client.get("/api/checklists/9999/shares").status_code == 404
    # 清单删除 → 分享随之删除（公开视图 404）
    s = client.post(f"/api/checklists/{c['id']}/share", json={}).json()
    assert client.delete(f"/api/checklists/{c['id']}").status_code == 204
    assert client.get(f"/api/share/{s['token']}").status_code == 404
    assert client.get("/api/shares").json() == []


# ---------------------------------------------------------------------------
# 一键导入
# ---------------------------------------------------------------------------

def test_import_from_payload(client):
    course = _course(client)
    payload = {
        "token": "t",
        "title": "别人的四六级清单",
        "description": "d",
        "course_name": course["name"],
        "tags": ["四六级"],
        "items": [
            {"text": "背单词", "done": True, "note": "每天50"},
            {"text": "", "done": True, "note": ""},  # 空项剔除
        ],
        "due_at": None,
        "created_at": "2025-01-01T00:00:00Z",
        "expires_at": None,
        "view_count": 0,
        "is_owner": False,
    }
    r = client.post("/api/checklists/import", json={"payload": payload})
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["title"] == "别人的四六级清单"
    assert d["course_id"] == course["id"], "课程按名字匹配到本机已有课程"
    assert d["items"] == [{"text": "背单词", "done": False, "note": "每天50"}], "勾选清零、空项剔除"

    # 课程匹配不到 → 不绑课程；空 body / 缺标题 → 422
    payload["course_name"] = "不存在的课"
    r = client.post("/api/checklists/import", json={"payload": payload})
    assert r.status_code == 201 and r.json()["course_id"] is None
    assert client.post("/api/checklists/import", json={}).status_code == 422
    assert client.post("/api/checklists/import", json={"payload": {"title": " "}}).status_code == 422


def test_import_from_url(client, monkeypatch):
    import httpx

    from app.api.routers import checklists as mod

    course = _course(client)
    payload = {
        "token": "abc",
        "title": "分享来的清单",
        "description": "",
        "course_name": course["name"],
        "tags": [],
        "items": [{"text": "做真题", "done": True, "note": ""}],
        "due_at": None,
        "created_at": "2025-01-01T00:00:00Z",
        "expires_at": None,
        "view_count": 3,
        "is_owner": False,
    }
    calls: dict = {}

    class FakeResp:
        status_code = 200

        def json(self):
            return payload

    def fake_get(url, **kw):
        calls["url"] = url
        calls["kw"] = kw
        return FakeResp()

    monkeypatch.setattr(mod.httpx, "get", fake_get)
    # 页面链接 → 自动归一化为 API 地址
    r = client.post("/api/checklists/import", json={"url": "http://10.0.0.2:8800/#/s/abc?x=1"})
    assert r.status_code == 201, r.text
    assert calls["url"] == "http://10.0.0.2:8800/api/share/abc"
    assert calls["kw"]["timeout"] == 10.0
    d = r.json()
    assert d["title"] == "分享来的清单"
    assert d["course_id"] == course["id"]
    assert d["items"][0]["done"] is False

    # 直接给 API 地址也行
    client.post("/api/checklists/import", json={"url": "http://10.0.0.2:8800/api/share/abc"})
    assert calls["url"] == "http://10.0.0.2:8800/api/share/abc"

    # 非法链接 422
    for bad in ("", "ftp://x", "not-a-url", "http://x/#/s/", "http://x/other"):
        assert client.post("/api/checklists/import", json={"url": bad}).status_code == 422

    # 远端不可达 / 非 200 / 非 JSON → 502
    def raise_conn(url, **kw):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(mod.httpx, "get", raise_conn)
    assert client.post(
        "/api/checklists/import", json={"url": "http://10.0.0.2:8800/api/share/abc"}
    ).status_code == 502

    class BadStatus:
        status_code = 410

        def json(self):
            return {"detail": "gone"}

    monkeypatch.setattr(mod.httpx, "get", lambda url, **kw: BadStatus())
    assert client.post(
        "/api/checklists/import", json={"url": "http://10.0.0.2:8800/api/share/abc"}
    ).status_code == 502

    class BadJson:
        status_code = 200

        def json(self):
            raise ValueError("not json")

    monkeypatch.setattr(mod.httpx, "get", lambda url, **kw: BadJson())
    assert client.post(
        "/api/checklists/import", json={"url": "http://10.0.0.2:8800/api/share/abc"}
    ).status_code == 502


def test_share_roundtrip_with_import(client):
    """端到端：分享 → 公开 JSON → payload 导入（模拟同学一键导入）。"""
    _seed_templates()
    c = _checklist(client, tags=["期末"])
    s = client.post(f"/api/checklists/{c['id']}/share", json={}).json()
    shared = client.get(f"/api/share/{s['token']}").json()
    imported = client.post("/api/checklists/import", json={"payload": shared}).json()
    assert imported["title"] == c["title"]
    assert imported["items"] == [
        {"text": "整理错题", "done": False, "note": ""},
        {"text": "做一套真题", "done": False, "note": "计时"},
    ]
    assert imported["id"] != c["id"]
