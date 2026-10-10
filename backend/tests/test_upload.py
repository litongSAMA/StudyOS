"""文件上传：TXT / PDF 文本抽取 + 截图 OCR 降级路径 + 未知扩展名。"""
import base64
import tempfile
from datetime import datetime, time
from pathlib import Path

import helpers


def test_upload_txt(client):
    content = (
        "计算机网络期中考试复习范围：TCP三次握手与拥塞控制，"
        "考试时间 11月30日下午3点。请同学们提前复习。"
    ).encode("utf-8")
    r = client.post("/api/materials/upload", files={
        "file": ("复习通知.txt", content, "text/plain"),
    })
    assert r.status_code == 200
    body = r.json()
    tasks = body["extract"]["tasks"]
    assert tasks
    assert datetime.fromisoformat(tasks[0]["due_at"]) == helpers.expect_utc(
        helpers.expect_abs(11, 30, time(15, 0))
    )
    assert tasks[0]["kind"] == "exam"
    assert "TCP" in body["extracted_text_preview"]
    # 文件已落盘（临时数据目录）
    assert Path(body["stored_path"]).exists()


def test_upload_pdf(client):
    """真实 PDF（PyMuPDF 生成）→ 文本抽取 → DDL 识别。"""
    import pytest

    import importlib.util
    import subprocess
    import sys
    if not importlib.util.find_spec('pymupdf'):
        pytest.skip('PyMuPDF unavailable')
    path = Path(tempfile.gettempdir()) / "studyos-test-deadline.pdf"
    subprocess.run([sys.executable, '-c', "import pymupdf,sys; doc=pymupdf.open(); page=doc.new_page(); page.insert_text((72,100),'Operating Systems homework. The deadline is due 2026-12-01 23:59. Please submit to the TA mailbox.'); doc.save(sys.argv[1]); doc.close()",str(path)],check=True)

    with open(path, "rb") as f:
        r = client.post("/api/materials/upload", files={
            "file": ("os_homework.pdf", f, "application/pdf"),
        })
    assert r.status_code == 200
    tasks = r.json()["extract"]["tasks"]
    assert tasks
    assert datetime.fromisoformat(tasks[0]["due_at"]) == helpers.expect_utc(
        datetime(2026, 12, 1, 23, 59)
    )


def test_upload_image_without_ocr(client):
    """OCR 关闭/不可用 → 截图上传降级为人工确认提示，不崩溃。"""
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
    )
    r = client.post("/api/materials/upload", files={
        "file": ("课程群截图.png", png, "image/png"),
    })
    assert r.status_code == 200
    warnings = r.json()["extract"]["warnings"]
    assert any("扫描件" in w for w in warnings)


def test_upload_image_pixel_bomb(client):
    """图片头声明 20000×20000（文件仅 57 字节）→ 413 拦截且不落盘。"""
    from app.infra.filestore import files_root

    root = files_root()
    before = set(root.iterdir())
    r = client.post("/api/materials/upload", files={
        "file": ("炸弹.png", helpers.png_bytes(20000, 20000), "image/png"),
    })
    assert r.status_code == 413
    assert "像素" in r.json()["detail"] or "尺寸" in r.json()["detail"]
    assert set(root.iterdir()) == before  # 拦截后不留残留文件


def test_upload_unknown_ext(client):
    r = client.post("/api/materials/upload", files={
        "file": ("notes.xyz", b"whatever", "application/octet-stream"),
    })
    assert r.status_code == 200
    assert r.json()["extract"]["tasks"] == []
