"""格式登记表与内容嗅探：扩展名覆盖、魔数纠正、目录接口。

这些用例是「常见文件类型格式全覆盖」的第一道防线：
登记表少一个扩展名、嗅探把 docx 认成 zip、目录接口漏一个分组，
都会在这里失败，而不是等到用户上传时才发现。
"""
from __future__ import annotations

import io
import zipfile

import pytest

from app.infra.ingest import formats as F

#: 「常见学习文件」必须在登记表里出现的最小集合（按分组）
REQUIRED = {
    "document": [".pdf", ".docx", ".doc", ".odt", ".rtf", ".txt", ".md", ".tex"],
    "slide": [".pptx", ".ppt", ".odp"],
    "sheet": [".xlsx", ".xlsm", ".xls", ".ods", ".csv", ".tsv"],
    "ebook": [".epub", ".mobi", ".azw3"],
    "notebook": [".ipynb"],
    "markup": [".html", ".htm", ".xhtml", ".xml"],
    "data": [".json", ".yaml", ".yml", ".toml", ".ini", ".ics"],
    "code": [".py", ".js", ".ts", ".java", ".c", ".cpp", ".go", ".rs", ".sql", ".sh"],
    "subtitle": [".srt", ".vtt", ".ass", ".lrc"],
    "image": [".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"],
    "archive": [".zip"],
    "media": [".mp3", ".wav", ".mp4", ".mov"],
}


def test_required_formats_registered():
    """每个分组的关键扩展名都已登记，且有中文名与抽取 kind。"""
    for group, exts in REQUIRED.items():
        for ext in exts:
            spec = F.spec_for(ext)
            assert spec is not None, f"{ext} 未登记"
            assert spec.group == group, f"{ext} 分组应为 {group}，实际 {spec.group}"
            assert spec.label, f"{ext} 缺少中文名"
            assert spec.kind, f"{ext} 缺少抽取 kind"


def test_format_count_and_groups():
    """登记表规模与分组完整性（防止误删导致大面积能力回退）。"""
    assert len(F.FORMATS) >= 90
    assert set(F.GROUP_ORDER) >= {s.group for s in F.FORMATS.values()}
    for group in {s.group for s in F.FORMATS.values()}:
        assert group in F.GROUP_LABELS, f"{group} 缺少分组中文名"


def test_alias_and_normalize():
    assert F.normalize_ext("作业.JPEG") == ".jpeg"      # .jpeg 本身也是登记格式
    assert F.normalize_ext("照片.JFIF") == ".jpg"        # 别名归并到规范扩展名
    assert F.normalize_ext("a/b/复习.MD") == ".md"
    assert F.normalize_ext("无扩展名") == ""
    assert F.normalize_ext("") == ""
    assert F.spec_for("扫描件.JFIF").kind == "image"


def test_accept_string_matches_registry():
    """前端 accept 串必须与登记表一一对应（不许出现未登记的扩展名）。"""
    exts = F.accept_string().split(",")
    assert len(exts) == len(F.FORMATS)
    assert set(exts) == set(F.FORMATS)


def test_catalog_shape():
    cat = F.catalog(ocr_available=True)
    assert cat["ocr_available"] is True
    ids = [g["id"] for g in cat["groups"]]
    assert ids[0] == "document" and "image" in ids and "media" in ids
    flat = [item for g in cat["groups"] for item in g["formats"]]
    assert len(flat) == len(F.FORMATS)
    media = [i for i in flat if i["ext"] == ".mp3"][0]
    assert media["supported"] is False and media["note"]
    pdf = [i for i in flat if i["ext"] == ".pdf"][0]
    assert pdf["supported"] is True and pdf["label"] == "PDF 文档"
    assert cat["refine_accept"] == F.refine_accept_string()


def test_refine_accept_covers_documents_but_not_media():
    """「AI 笔记提炼」的 accept 串：PDF/Word/Excel/图片在内，音视频与未知在外。

    提炼面板直接把这个串喂给 `<input accept>`，因此它必须与后端可直传的集合
    完全一致（多一个 = 用户能选却提炼不了，少一个 = 能力被前端藏起来）。
    """
    exts = set(F.refine_accept_string().split(","))
    for ext in (".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt", ".txt",
                ".md", ".csv", ".epub", ".png", ".jpg", ".webp", ".zip"):
        assert ext in exts, f"{ext} 应可直接用于笔记提炼"
    for ext in (".mp3", ".mp4", ".mov", ".wav"):
        assert ext not in exts, f"{ext} 无法离线转写，不应出现在提炼 accept 里"
    assert exts == {s.ext for s in F.refine_specs()}


def test_refine_specs_and_is_refinable():
    assert F.is_refinable("第三章 讲义.PDF") is True
    assert F.is_refinable("成绩单.xlsx") is True
    assert F.is_refinable("课堂板书.png") is True
    assert F.is_refinable("讲座录音.mp3") is False
    assert F.is_refinable("未知文件.bin") is False
    assert F.is_refinable("") is False
    assert all(s.supported for s in F.refine_specs())
    assert F.REFINE_EXCLUDED_GROUPS == ("media", "other")


def test_heic_marked_unsupported_without_decoder():
    """HEIC 没有解码器时要降级标记（promise 的能力必须真的做得到）。"""
    cat = F.catalog()
    items = {i["ext"]: i for g in cat["groups"] for i in g["formats"]}
    heic = items[".heic"]
    assert heic["supported"] is F.decoder_available(".heic")
    if not heic["supported"]:
        assert "JPG" in heic["note"] or "解码" in heic["note"]


# ---------------------------------------------------------------------------
# 内容嗅探：不信任扩展名
# ---------------------------------------------------------------------------

def test_sniff_by_magic():
    assert F.sniff(b"%PDF-1.7\n...") == ".pdf"
    assert F.sniff(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20) == ".png"
    assert F.sniff(b"\xff\xd8\xff\xe0" + b"\x00" * 20) == ".jpg"
    assert F.sniff(b"GIF89a" + b"\x00" * 20) == ".gif"
    assert F.sniff(b"{\\rtf1\\ansi hello}") == ".rtf"
    assert F.sniff(b"ID3\x03\x00\x00\x00" + b"\x00" * 30) == ".mp3"
    assert F.sniff(b"RIFF\x00\x00\x00\x00WAVEfmt ") == ".wav"


def test_sniff_office_containers():
    """OOXML/ODF 靠容器内部结构区分（都是 ZIP 魔数）。"""
    assert F.sniff(_zip({"word/document.xml": "<w/>"})) == ".docx"
    assert F.sniff(_zip({"xl/workbook.xml": "<x/>"})) == ".xlsx"
    assert F.sniff(_zip({"ppt/presentation.xml": "<p/>"})) == ".pptx"
    assert F.sniff(_zip({"mimetype": "application/vnd.oasis.opendocument.text",
                         "content.xml": "<x/>"})) == ".odt"
    assert F.sniff(_zip({"mimetype": "application/vnd.oasis.opendocument.spreadsheet",
                         "content.xml": "<x/>"})) == ".ods"
    assert F.sniff(_zip({"mimetype": "application/vnd.oasis.opendocument.presentation",
                         "content.xml": "<x/>"})) == ".odp"
    assert F.sniff(_zip({"mimetype": "application/epub+zip",
                         "META-INF/container.xml": "<c/>"})) == ".epub"
    assert F.sniff(_zip({"随便.txt": "hi"})) == ".zip"


def test_sniff_ole2_streams():
    """老式 .doc/.xls/.ppt 都是 OLE2 容器，靠目录区里的流名区分。"""
    ole = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64
    assert F.sniff(ole + "WordDocument".encode("utf-16-le")) == ".doc"
    assert F.sniff(ole + "Workbook".encode("utf-16-le")) == ".xls"
    assert F.sniff(ole + "PowerPoint Document".encode("utf-16-le")) == ".ppt"


def test_sniff_text_kinds():
    assert F.sniff("<!DOCTYPE html><html><body>x</body></html>".encode()) == ".html"
    assert F.sniff('<?xml version="1.0"?><a/>'.encode()) == ".xml"
    assert F.sniff("WEBVTT\n\n00:00.000 --> 00:02.000\nhi".encode()) == ".vtt"
    assert F.sniff("BEGIN:VCALENDAR\nVERSION:2.0".encode()) == ".ics"
    assert F.sniff('{"a": 1}'.encode()) == ".json"
    assert F.sniff("随便一段中文，不是任何结构化格式".encode()) == ""
    assert F.sniff(b"") == ""


def test_detect_prefers_declared_for_specialized_format():
    """`.ipynb` 内容是 JSON，但必须按 Notebook 解析而不是当普通 JSON。"""
    nb = b'{"cells": [], "nbformat": 4}'
    info = F.detect_format("作业.ipynb", data=nb)
    assert info.spec.kind == "ipynb"
    assert info.mismatch is False


def test_detect_flags_mismatch():
    """扩展名与真实内容不符要打标记（并给出实际格式）。"""
    info = F.detect_format("冒充的.pptx", data=b"%PDF-1.4 ...")
    assert info.spec.ext == ".pdf"
    assert info.mismatch is True
    assert info.declared_ext == ".pptx"


def test_detect_text_extension_never_mismatch():
    """.txt 里放 JSON/HTML 是常见用法，不该报「格式不一致」。"""
    assert F.detect_format("笔记.txt", data=b'{"a": 1}').mismatch is False
    assert F.detect_format("笔记.txt", data="<html><body>x</body></html>".encode()).mismatch is False


def test_detect_unknown():
    info = F.detect_format("东西.zzz", data=b"\x00\x01\x02\x03binary")
    assert info.kind == "unknown"
    assert info.supported is False
    assert "转成" in info.spec.note
    assert F.detect_format("空文件.zzz").kind == "unknown"


def test_detect_by_extension_when_content_unknown():
    """内容认不出（纯文本）时按扩展名判定。"""
    info = F.detect_format("课表.csv", data="周一,周二\n数学,英语\n".encode())
    assert info.spec.ext == ".csv"
    assert info.sniffed is False


def test_describe_and_group_helpers():
    assert F.describe("a.docx")["label"] == "Word 文档"
    assert F.describe("a.zzz")["group"] == "other"
    assert F.group_of("a.py") == "code"
    assert F.is_image_ext("x.HEIC") is True
    assert F.is_image_ext("x.pdf") is False
    assert "pdf" in F.kinds_for([".pdf", ".PDF", ".docx"])


def test_decoder_available():
    """常见图片格式在本机必须真的可解码（否则 OCR 会报「文件已损坏」）。"""
    for ext in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"):
        assert F.decoder_available(ext) is True, f"{ext} 本机不可解码"
    assert F.decoder_available(".pdf") is True
    assert F.decoder_available("") is True


def test_sniff_zip_for_path_input(tmp_path):
    """按路径嗅探（上传接口就是这么用的）：不整体载入内存也能识别。"""
    target = tmp_path / "课件.docx"
    target.write_bytes(_zip({"word/document.xml": "<w/>"}))
    assert F.sniff(target) == ".docx"
    assert F.detect_format(target.name, path=target).spec.kind == "docx"


def test_sniff_container_reads_mimetype_of_large_zip(tmp_path):
    """带 mimetype 的 ODF 走内容判定，而不是被「content.xml」误判。"""
    target = tmp_path / "表格.ods"
    target.write_bytes(_zip({
        "mimetype": "application/vnd.oasis.opendocument.spreadsheet",
        "content.xml": "<x/>",
        "Object 1/whatever": "x",
    }))
    assert F.sniff(target) == ".ods"


@pytest.mark.parametrize("ext", sorted(F.FORMATS))
def test_every_registered_format_has_accept_or_note(ext):
    """每个登记项都要么可用、要么给出可操作说明（不允许「登记了但没交代」）。"""
    spec = F.spec_for(ext)
    assert spec is not None
    if not spec.supported:
        assert spec.note, f"{ext} 标记为不支持但没有 note 指引"


def _zip(entries: dict[str, str]) -> bytes:
    """构造一个最小 ZIP（用于容器嗅探用例）。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return buf.getvalue()
