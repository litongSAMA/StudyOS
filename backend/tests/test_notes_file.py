"""文档提炼接口（/refine-file）：PDF / Word / Excel / PPT / 文本 / 图片全覆盖。

用例用**真实文件**（PyMuPDF、python-docx、openpyxl 现场生成）走完整链路：
multipart 上传 → 落盘 → 内容嗅探 → 抽取器（扫描件还会自动转 OCR）→ 提炼管线，
并断言回包的格式元信息与抽取提示——前端要靠它们渲染格式徽标与核对原文。
"""
from __future__ import annotations

import base64
import io

import pytest

import app.api.routers.notes as notes_router

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)
DEADLOCK = (
    "操作系统第三章重点：死锁的四个必要条件是互斥、请求与保持、不可剥夺、循环等待。"
    "考试重点：银行家算法的安全性检测步骤，先求 Need 矩阵再看是否存在安全序列。"
)


def upload(client, name, data, mode="keypoints", content_type="application/octet-stream"):
    return client.post(
        "/api/notes/refine-file",
        files={"file": (name, data, content_type)},
        data={"mode": mode},
    )


def ok(client, name, data, **kw):
    r = upload(client, name, data, **kw)
    assert r.status_code == 200, r.text
    return r.json()


def fake_ocr(monkeypatch, text):
    """模拟「OCR 可用且识别出 text」（抽取链路的图片通道）。"""
    monkeypatch.setattr(notes_router.ocr, "ocr_available", lambda: True)
    monkeypatch.setattr(notes_router.ocr, "ocr_image", lambda path: text)


# ---------------------------------------------------------------------------
# 生成真实文件
# ---------------------------------------------------------------------------

def pdf_bytes(text: str) -> bytes:
    """带文本层的 PDF（PyMuPDF 必须经 office._pymupdf 导入，见其说明）。"""
    from app.infra.ingest.office import _pymupdf

    pymupdf = _pymupdf()
    if pymupdf is None:  # pragma: no cover - 缺依赖时跳过
        pytest.skip("PyMuPDF unavailable")
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 96), text, fontname="china-s", fontsize=12)
    data = doc.tobytes()
    doc.close()
    return data


def docx_bytes(paragraphs: list[str]) -> bytes:
    from docx import Document

    document = Document()
    for line in paragraphs:
        document.add_paragraph(line)
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


def xlsx_bytes(rows: list[list[str]]) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "复习"
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def pptx_bytes(title: str, body: str) -> bytes:
    from pptx import Presentation

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[5])
    slide.shapes.title.text = title
    box = slide.shapes.add_textbox(0, 0, 400, 100)
    box.text_frame.text = body
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# 文档：PDF / Word / Excel / PPT / 文本
# ---------------------------------------------------------------------------

def test_refine_pdf_text_layer(client):
    """PDF：文本层直接抽取，格式元信息与原文都回给前端。"""
    body = ok(client, "第三章 死锁.pdf", pdf_bytes(DEADLOCK))
    assert body["file_ext"] == ".pdf"
    assert body["format_label"] == "PDF 文档"
    assert body["format_group"] == "document"
    assert body["source_kind"] == "document"
    assert body["file_name"] == "第三章 死锁.pdf"
    assert body["text_chars"] > 0
    assert body["source_text"] == body["source_text"].strip()
    assert "死锁" in body["source_text"]
    assert body["points"], "应提炼出要点"
    assert any("死锁" in p["point"] or "银行家" in p["point"] for p in body["points"])


def test_refine_docx(client):
    """Word：正文进提炼管线，标题默认取文件名（去掉扩展名）。"""
    data = docx_bytes(["第三章 内存管理", "分页与分段的主要区别在于地址空间的划分方式。"])
    body = ok(client, "操作系统第三章.docx", data)
    assert body["file_ext"] == ".docx"
    assert body["format_label"] == "Word 文档"
    assert body["format_group_label"] == "文档"
    assert "分页" in body["source_text"]
    assert body["title"] == "操作系统第三章"      # 扩展名不进标题


def test_refine_xlsx(client):
    """Excel：多行单元格渲染成表格文本后提炼（成绩/公式表也能提炼考点）。"""
    data = xlsx_bytes([["章节", "考点"], ["3.1 分页", "页表与快表"], ["3.2 分段", "段表"]])
    body = ok(client, "复习提纲.xlsx", data)
    assert body["file_ext"] == ".xlsx"
    assert body["format_group"] == "sheet"
    assert body["format_group_label"] == "表格"
    assert "页表" in body["source_text"] and "分段" in body["source_text"]


def test_refine_pptx(client):
    """PPT：正文与演讲者备注都算考点来源。"""
    data = pptx_bytes("进程调度算法", "先来先服务 / 短作业优先，考试重点：周转时间计算")
    body = ok(client, "第4讲 调度.pptx", data)
    assert body["file_ext"] == ".pptx"
    assert "进程调度" in body["source_text"]


def test_refine_plain_text_and_markdown(client):
    """纯文本 / Markdown：走文本抽取器，不经过 OCR。"""
    body = ok(client, "课堂笔记.md", DEADLOCK.encode("utf-8"))
    assert body["source_kind"] == "document"
    assert "死锁" in body["source_text"]
    assert body["ocr_available"] is False        # 测试环境 OCR 关闭，但不影响文档通道


def test_refine_csv(client):
    """CSV：按表格渲染成对齐文本。"""
    data = "章节,考点\n3.1,页表与快表\n".encode("utf-8")
    body = ok(client, "复习.csv", data)
    assert body["file_ext"] == ".csv"
    assert "页表" in body["source_text"]


def test_refine_file_saves_as_note(client):
    """提炼 → 保存为笔记：预览不落库，确认后 `POST /api/notes` 才入库。"""
    body = ok(client, "死锁.pdf", pdf_bytes(DEADLOCK), mode="exam")
    assert client.get("/api/notes").json() == []           # 预览阶段没有副作用
    saved = client.post("/api/notes", json={
        "title": body["title"], "mode": body["mode"],
        "summary": body["summary"], "points": body["points"], "engine": body["engine"],
    })
    assert saved.status_code == 201
    notes = client.get("/api/notes").json()
    assert len(notes) == 1 and notes[0]["mode"] == "exam"


# ---------------------------------------------------------------------------
# 图片通道：与识图提炼同一条链路
# ---------------------------------------------------------------------------

def test_refine_file_image_uses_ocr(client, monkeypatch):
    """图片：/refine-file 与 /refine-image 行为一致（OCR → 提炼）。"""
    fake_ocr(monkeypatch, "计算机网络：TCP 三次握手的过程。")
    body = ok(client, "板书.png", PNG, content_type="image/png")
    assert body["source_kind"] == "image"
    assert body["format_group"] == "image"
    assert body["ocr_available"] is True
    assert "三次握手" in body["source_text"]
    assert body["warnings"] and "OCR" in "；".join(body["warnings"])


def test_refine_file_image_without_ocr_guidance(client):
    """OCR 关闭时图片 → 422 明确指引（不必让用户猜为什么失败）。"""
    r = upload(client, "板书.png", PNG, content_type="image/png")
    assert r.status_code == 422
    assert "识别出文字" in r.json()["detail"]


def test_refine_image_endpoint_still_rejects_documents(client):
    """/refine-image 是图片专用入口：文档要给「改用文档提炼」的指引。"""
    r = client.post(
        "/api/notes/refine-image",
        files={"file": ("讲义.docx", docx_bytes(["第三章 死锁"]), "application/octet-stream")},
        data={"mode": "keypoints"},
    )
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert "图片" in detail and ("文档" in detail or "资料入库" in detail)


# ---------------------------------------------------------------------------
# 格式纠正与提示
# ---------------------------------------------------------------------------

def test_refine_file_corrects_mismatched_extension(client):
    """扩展名不可信：.ppt 实际是 PPTX → 按内容解析并提示不一致。"""
    data = pptx_bytes("文件系统", "inode 与索引节点，考试重点：磁盘调度算法")
    body = ok(client, "第四讲.ppt", data)
    assert body["file_ext"] == ".pptx"
    assert body["format_label"] == "PowerPoint 演示文稿"
    assert any("不一致" in w for w in body["warnings"])


def test_refine_file_truncates_huge_source_text(client):
    """超长正文：source_text 截断到 2 万字（回传体积可控），字数与截断标记照实回。"""
    text = ("第三章 文件系统：索引节点与目录实现。" * 1200)     # ≈ 2.4 万字
    body = ok(client, "超大讲义.txt", text.encode("utf-8"))
    assert body["text_chars"] > notes_router.MAX_SOURCE_TEXT
    assert len(body["source_text"]) == notes_router.MAX_SOURCE_TEXT
    assert body["truncated"] is True


def test_refine_file_title_hint_wins(client):
    body = client.post(
        "/api/notes/refine-file",
        files={"file": ("讲义.pdf", pdf_bytes(DEADLOCK), "application/octet-stream")},
        data={"mode": "keypoints", "title_hint": "第三章 死锁与银行家算法"},
    ).json()
    assert body["title"] == "第三章 死锁与银行家算法"


# ---------------------------------------------------------------------------
# 拒绝路径：不支持的格式 / 抽不到文字 / 体积与参数
# ---------------------------------------------------------------------------

def test_refine_file_media_guidance(client):
    """音视频：不是「无法识别」，而是「本机不能离线转写」+ 下一步动作。"""
    r = upload(client, "讲座录音.mp3", b"ID3\x03\x00\x00\x00" + b"\x00" * 64)
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert "转写" in detail and "课件" in detail


def test_refine_file_unknown_type(client):
    r = upload(client, "奇怪文件.bin", b"\x00\x01\x02\x03" * 64)
    assert r.status_code == 422
    assert "未能识别" in r.json()["detail"]


def test_refine_file_empty_document_guidance(client):
    """空 Word（只有空段落）→ 422 说明「空文件或正文都是图片」。"""
    r = upload(client, "空的.docx", docx_bytes(["", "   "]))
    assert r.status_code == 422
    assert "抽取到文字" in r.json()["detail"]


def test_refine_file_scanned_pdf_without_ocr(client):
    """扫描版 PDF（无文本层）且 OCR 不可用 → 指引换文字版或粘贴文本。"""
    from app.infra.ingest.office import _pymupdf

    pymupdf = _pymupdf()
    if pymupdf is None:  # pragma: no cover
        pytest.skip("PyMuPDF unavailable")
    doc = pymupdf.open()
    doc.new_page()
    data = doc.tobytes()
    doc.close()
    r = upload(client, "扫描件.pdf", data)
    assert r.status_code == 422
    assert "扫描件" in r.json()["detail"]


def test_refine_file_too_large(client, monkeypatch):
    monkeypatch.setattr(notes_router, "MAX_DOC_BYTES", 16)
    r = upload(client, "大讲义.pdf", pdf_bytes(DEADLOCK))
    assert r.status_code == 413
    assert "过大" in r.json()["detail"]


def test_refine_file_big_image_blocked_by_image_limit(client, monkeypatch):
    """图片另有一条更严的体积线（20MB），文档上限放宽不能顺带放宽图片。"""
    monkeypatch.setattr(notes_router, "MAX_IMAGE_BYTES", 8)
    r = upload(client, "板书.png", PNG, content_type="image/png")
    assert r.status_code == 413
    assert "图片过大" in r.json()["detail"]


def test_refine_file_bad_mode(client):
    r = upload(client, "讲义.txt", DEADLOCK.encode("utf-8"), mode="weird")
    assert r.status_code == 422
    assert "模式" in r.json()["detail"]


def test_refine_file_pixel_bomb_rejected(client):
    """解压炸弹防护在文件提炼通道同样生效（图片像素校验先于 OCR）。"""
    import helpers

    r = upload(client, "炸弹.png", helpers.png_bytes(20000, 20000), content_type="image/png")
    assert r.status_code == 413
    assert "像素" in r.json()["detail"] or "尺寸" in r.json()["detail"]
