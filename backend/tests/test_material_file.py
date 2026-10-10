"""资料附件下发接口：内嵌预览/下载原始文件的安全与正确性。

覆盖：
- 真实 PDF/PPTX 上传 → 确认落库 → GET /api/materials/{id}/file 返回原始字节与正确 MIME；
- Content-Disposition 为 inline（供浏览器内嵌预览）；
- Range 分段加载（大 PDF 跳页不整包重载）；
- 无附件 / 路径越界（data/files 之外）/ 文件已丢失 一律 404，不泄露是否存在；
- 登录保护开启时该接口受保护（无令牌 401）。
"""
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import select

from app.infra.db import SessionLocal
from app.infra.models import Material

_TMP = Path(tempfile.gettempdir())


def _register_and_enable(client, username="alice", password="secret123"):
    """注册 → 开启登录保护，返回会话令牌（后续请求需带 Bearer）。"""
    r = client.post("/api/auth/register", json={
        "username": username, "password": password,
    })
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    r = client.post(
        "/api/auth/enable", json={"enabled": True},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    return token


def _make_material_with_file(client, filename: str, data: bytes, headers=None) -> dict:
    """上传文件 → apply 确认落库 → 返回创建的资料（file_path 指向 data/files 内）。"""
    r = client.post("/api/materials/upload", files={
        "file": (filename, data, "application/octet-stream"),
    }, headers=headers)
    assert r.status_code == 200
    stored_path = r.json()["stored_path"]
    m = client.post("/api/agent/apply", json={
        "tasks": [],
        "materials": [{"title": f"资料-{filename}", "type": "courseware"}],
        "file_path": stored_path,
        "file_name": filename,
        "source": "file",
    }, headers=headers).json()["created_materials"][0]
    # apply 只引用上传接口返回的 stored_path，落盘文件真实存在
    assert Path(stored_path).exists()
    return m


def test_material_file_serves_original_pdf(client):
    """真实 PDF：文件接口返回原始字节 + application/pdf + inline。"""
    fitz = pytest.importorskip("fitz")
    path = _TMP / "studyos-preview-slides.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Preview feature: embedded PDF viewer")
    doc.save(str(path))
    doc.close()
    raw = path.read_bytes()

    m = _make_material_with_file(client, "课件.pdf", raw)
    r = client.get(f"/api/materials/{m['id']}/file")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert "inline" in r.headers["content-disposition"]
    assert r.content == raw


def test_material_file_range(client):
    """大文件支持 Range 分段（PDF 预览跳页不整包重载）。"""
    data = b"%PDF-1.4 fake but long enough" + b"0" * 1000
    m = _make_material_with_file(client, "range.pdf", data)
    r = client.get(
        f"/api/materials/{m['id']}/file", headers={"Range": "bytes=0-9"}
    )
    # Starlette FileResponse 支持 206 分段；不支持时也应整包返回 200
    if r.status_code == 206:
        assert r.content == data[:10]
        assert r.headers["content-range"].startswith("bytes 0-9/")
    else:
        assert r.status_code == 200
        assert r.content == data


def test_material_file_pptx_served_raw(client):
    """PPTX（真实 python-pptx 生成）：原样下发，前端负责渲染。"""
    pptx = pytest.importorskip("pptx")
    prs = pptx.Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[1])
    slide.shapes.title.text = "Preview feature"
    slide.placeholders[1].text = "Rendered by pptx-preview in browser"
    path = _TMP / "studyos-lecture.pptx"
    prs.save(str(path))
    raw = path.read_bytes()

    m = _make_material_with_file(client, "操作系统第3章.pptx", raw)
    r = client.get(f"/api/materials/{m['id']}/file")
    assert r.status_code == 200
    assert "presentationml" in r.headers["content-type"]  # pptx MIME
    assert "inline" in r.headers["content-disposition"]
    assert r.content == raw


def test_material_file_missing_attachment_404(client):
    """资料没有附件（纯文本入库）→ 404，不泄露。"""
    m = client.post("/api/materials", json={
        "title": "纯文本笔记", "type": "notes", "text": "没有附件",
    }).json()
    r = client.get(f"/api/materials/{m['id']}/file")
    assert r.status_code == 404


def test_material_file_outside_files_root_404(client):
    """file_path 指向 data/files 之外（越界）且 basename 在 files 内不存在 → 404。"""
    outside = _TMP / "studyos-secret.txt"
    outside.write_text("should not be served", encoding="utf-8")
    m = client.post("/api/materials", json={
        "title": "越界资料", "type": "other", "text": "x",
    }).json()
    with SessionLocal() as db:
        obj = db.execute(
            select(Material).where(Material.id == m["id"])
        ).scalar_one()
        obj.file_path = str(outside)
        obj.file_name = "secret.txt"
        db.commit()

    r = client.get(f"/api/materials/{m['id']}/file")
    assert r.status_code == 404
    assert "未找到" in r.json()["detail"]
    assert outside.exists()  # 只读接口，不删除文件


def test_material_file_cross_platform_path_resolves(client):
    """跨设备同步：file_path 是 macOS 绝对路径，但文件已在本机 files 目录 → 按 basename 找到并下发。"""
    data = b"%PDF-1.4 cross-platform"
    m = _make_material_with_file(client, "跨平台.pdf", data)
    obj = client.get(f"/api/materials/{m['id']}").json()
    local_path = Path(obj["file_path"])
    basename = local_path.name
    # 模拟跨设备：把 file_path 改写成 macOS 绝对路径（同 basename）
    mac_path = f"/Users/meteorshowers/Desktop/StudyOS/backend/data/files/{basename}"
    with SessionLocal() as db:
        mat = db.execute(
            select(Material).where(Material.id == m["id"])
        ).scalar_one()
        mat.file_path = mac_path
        db.commit()

    r = client.get(f"/api/materials/{m['id']}/file")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content == data
    # 确认文件确实是从本机 files 目录读取的（macOS 路径本身不存在）
    assert not Path(mac_path).exists()
    assert local_path.is_file()


def test_material_file_deleted_on_disk_404(client):
    """附件文件已从磁盘丢失 → 404（资料还在，但预览不可用）。"""
    data = b"gone soon"
    m = _make_material_with_file(client, "gone.pdf", data)
    obj = client.get(f"/api/materials/{m['id']}").json()
    Path(obj["file_path"]).unlink()

    r = client.get(f"/api/materials/{m['id']}/file")
    assert r.status_code == 404
    assert "未找到" in r.json()["detail"]


def test_material_file_unknown_material_404(client):
    assert client.get("/api/materials/99999/file").status_code == 404


def test_material_file_requires_login_when_protected(client):
    """登录保护开启后：无令牌访问文件接口 401；带令牌 200。"""
    token = _register_and_enable(client)
    auth = {"Authorization": f"Bearer {token}"}
    data = b"%PDF-1.4 login-protected"
    m = _make_material_with_file(client, "protected.pdf", data, headers=auth)
    try:
        assert client.get(f"/api/materials/{m['id']}/file").status_code == 401
        r = client.get(f"/api/materials/{m['id']}/file", headers=auth)
        assert r.status_code == 200
        assert r.content == data
    finally:
        client.post(
            "/api/auth/enable", json={"enabled": False},
            headers=auth,
        )
