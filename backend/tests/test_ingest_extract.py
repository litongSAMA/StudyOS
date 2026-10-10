"""抽取引擎单元用例：编码阶梯、RTF/字幕/Notebook 细节、压缩包护栏、OCR 兜底接线。

端到端用例（test_upload_formats.py）验证「格式能被识别并抽出正文」，
这里补充验证**边界与降级路径**：编码不能乱、恶意压缩包不能撑爆内存、
扫描版 PDF 在 OCR 可用/不可用两种情况下的行为都要可预期。
"""
from __future__ import annotations

import bz2
import gzip
import io
import lzma
import tarfile
import zipfile
from pathlib import Path

import pytest

from app.infra.ingest import files as ingest_files
from app.infra.ingest import archive, formats, office, textfmt
from app.infra.ingest.base import ExtractedFile, clean_text


def write(tmp_path: Path, name: str, data: bytes) -> Path:
    target = tmp_path / name
    target.write_bytes(data)
    return target


# ---------------------------------------------------------------------------
# 编码
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("encoding,text", [
    ("utf-8", "计算机网络期末复习范围"),
    ("gbk", "计算机网络期末复习范围"),
    ("gb18030", "计算机网络期末复习范围"),
    ("big5", "電腦網路期末複習範圍"),
    ("utf-16", "计算机网络期末复习范围"),
])
def test_encoding_ladder(tmp_path, encoding, text):
    """常见中文编码都要解对：乱码会让 DDL 抽取彻底失效。"""
    path = write(tmp_path, "通知.txt", text.encode(encoding))
    result = ingest_files.extract_file(path)
    assert text in result.text


def test_encoding_ladder_reports_non_utf8(tmp_path):
    """非 UTF-8 文件要提示编码，用户才知道为什么会看到个别怪字符。"""
    path = write(tmp_path, "名单.csv", "课程,教师\n高等数学,李四\n".encode("gbk"))
    result = ingest_files.extract_file(path)
    assert "高等数学" in result.text
    assert any("编码" in w for w in result.warnings)


def test_decode_text_bom_and_fallback():
    assert textfmt.decode_text("带BOM".encode("utf-8-sig"))[1] == "utf-8-sig"
    assert textfmt.decode_text(b"") == ("", "utf-8")
    text, enc = textfmt.decode_text(b"\xff\xfe" + "宽字符".encode("utf-16-le"))
    assert text == "宽字符" and enc == "utf-16"


def test_read_text_bounds_large_file(tmp_path):
    """大文件必须按字节上限读：上传上限 512MB，整载入会直接 OOM。"""
    path = tmp_path / "巨大.txt"
    path.write_bytes("中".encode("utf-8") * 1_000_000)      # 3MB
    text, enc = textfmt.read_text(path)                      # 默认 20 万字符上限
    assert enc == "utf-8"
    assert len(text) == textfmt.MAX_TEXT_CHARS
    # 抽取入口同样只保留上限内的正文
    assert len(ingest_files.extract_file(path).text) <= textfmt.MAX_TEXT_CHARS


def test_read_text_truncated_tail_still_decodes(tmp_path):
    """按字节截断会切掉最后一个汉字的一半，不能因此误判编码成乱码。"""
    path = tmp_path / "半截.txt"
    path.write_bytes("中".encode("utf-8") * 10)              # 30 字节
    text, enc = textfmt.read_text(path, limit=10, max_bytes=31)
    assert enc == "utf-8"
    assert text == "中" * 10


def test_rtf_text_bounded_read(tmp_path):
    """RTF 也按上限读，避免超大 .rtf 整载入；\\uN 转义照常还原。"""
    rtf = ("{\\rtf1\\ansi\\ansicpg936\\deff0 " + "\\u30002?" * 50 + "}").encode("latin-1")
    path = write(tmp_path, "大说明.rtf", rtf)
    text = ingest_files.extract_file(path).text
    assert text.count("甲") == 50                    # 十进制 30002 = 甲
    assert len(text) <= textfmt.MAX_TEXT_CHARS


# ---------------------------------------------------------------------------
# 文本整理
# ---------------------------------------------------------------------------

def test_clean_text_normalizes():
    messy = "第一行  \r\n\r\n\r\n\r\n第二行\u200b\x0c\n\n\n"
    assert clean_text(messy) == "第一行\n\n第二行"


def test_clean_text_truncates():
    assert len(clean_text("字" * 1000, limit=10)) == 10


# ---------------------------------------------------------------------------
# RTF / 字幕 / 数据
# ---------------------------------------------------------------------------

def test_rtf_unicode_escape_and_destinations(tmp_path):
    """\\uN 转义、字体表跳过、\\par 换行都要正确。"""
    rtf = (r"{\rtf1\ansi\deff0{\fonttbl{\f0\fnil\fcharset134 SimSun;}}"
           r"{\colortbl;\red0\green0\blue0;}\viewkind4\uc1\pard\f0\fs21 "
           r"\u31532?\u19977?\u31456?\par 6\u26376?1\u26085?\par}")
    path = write(tmp_path, "说明.rtf", rtf.encode("latin-1"))
    text = ingest_files.extract_file(path).text
    assert "第三章" in text and "6月1日" in text
    assert "SimSun" not in text         # 字体表是格式定义，不是正文
    assert "colortbl" not in text


def test_rtf_without_magic_is_not_parsed(tmp_path):
    path = write(tmp_path, "冒充.rtf", "这其实只是纯文本".encode("utf-8"))
    result = ingest_files.extract_file(path)
    assert result.text == ""           # 不是 RTF 就交给扩展名/内容判定，不硬解析


def test_ass_subtitle(tmp_path):
    ass = ("[Script Info]\nTitle: 网课\n[Events]\n"
           "Dialogue: 0,0:00:10.00,0:00:14.00,Default,,0,0,0,,{\\b1}第三章内存管理{\\b0}\n")
    path = write(tmp_path, "字幕.ass", ass.encode("utf-8"))
    text = ingest_files.extract_file(path).text
    assert "第三章内存管理" in text
    assert "{" not in text             # 特效标签要清掉


def test_lrc_subtitle(tmp_path):
    path = write(tmp_path, "听记.lrc", "[ti:课程]\n[00:12.00]第一章 概述\n[01:20.50]考点提示\n".encode("utf-8"))
    text = ingest_files.extract_file(path).text
    assert "[00:12] 第一章 概述" in text
    assert "[01:20] 考点提示" in text
    assert "ti:" not in text           # 元信息行不能当台词


def test_vtt_subtitle(tmp_path):
    vtt = "WEBVTT\n\n00:00:05.000 --> 00:00:09.000\n第二章 进程\n"
    path = write(tmp_path, "字幕.vtt", vtt.encode("utf-8"))
    text = ingest_files.extract_file(path).text
    assert "[00:05] 第二章 进程" in text


def test_json_summary_and_yaml(tmp_path):
    path = write(tmp_path, "数据.json", '{"courses": [1, 2, 3], "term": "2025"}'.encode("utf-8"))
    text = ingest_files.extract_file(path).text
    assert "[结构] courses[3]、term" in text
    yml = write(tmp_path, "配置.yml", "课程:\n  - 数学\n  - 英语\n".encode("utf-8"))
    assert "数学" in ingest_files.extract_file(yml).text


def test_ipynb_broken_json(tmp_path):
    path = write(tmp_path, "坏.ipynb", b"{not json")
    result = ingest_files.extract_file(path)
    assert result.text == ""
    assert any("JSON" in w or "Notebook" in w for w in result.warnings)


def test_table_text_row_limit(tmp_path):
    rows = "\n".join(f"{i},值{i}" for i in range(textfmt.MAX_TABLE_ROWS + 50))
    path = write(tmp_path, "大表.csv", ("序号,值\n" + rows).encode("utf-8"))
    text = ingest_files.extract_file(path).text
    assert "仅渲染前" in text
    assert len(text) < textfmt.MAX_TEXT_CHARS


# ---------------------------------------------------------------------------
# 压缩包护栏
# ---------------------------------------------------------------------------

def test_zip_skips_oversized_and_media_entries(tmp_path):
    """包内超大文件与音视频要跳过并说明，不能把内存吃光。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("正常.txt", "包内正常文本")
        zf.writestr("音频.mp3", b"ID3" + b"\x00" * 1024)
        zf.writestr("巨无霸.txt", "x" * (8 * 1024 * 1024 + 10))
    path = write(tmp_path, "包.zip", buf.getvalue())
    result = ingest_files.extract_file(path)
    assert "包内正常文本" in result.text
    assert any("跳过" in w for w in result.warnings)


def test_zip_without_parsable_content(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("图片.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    path = write(tmp_path, "只有图.zip", buf.getvalue())
    result = ingest_files.extract_file(path)
    assert result.text == ""
    assert any("压缩包" in w for w in result.warnings)
    # 包内图片不是「未抽取到文本」这么含糊：说清它要单独上传走 OCR
    assert any("图片" in w for w in result.warnings)


def test_archive_entry_cap_is_explained(tmp_path):
    """条目数触顶时要说「还有 N 个未解析」，否则用户以为资料丢了。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for i in range(archive.MAX_ENTRIES + 5):
            zf.writestr(f"第{i}份.txt", f"第 {i} 份正文")
    path = write(tmp_path, "很多份.zip", buf.getvalue())
    result = ingest_files.extract_file(path)
    assert result.text.count("[包内文件]") == archive.MAX_ENTRIES
    assert any("未解析" in w and str(archive.MAX_ENTRIES) in w for w in result.warnings)


def test_zip_ignores_nested_archive(tmp_path):
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as zf:
        zf.writestr("深层.txt", "深层内容不该被展开")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("外层.txt", "外层内容")
        zf.writestr("内层.zip", inner.getvalue())
    path = write(tmp_path, "套娃.zip", buf.getvalue())
    text = ingest_files.extract_file(path).text
    assert "外层内容" in text
    assert "深层内容" not in text       # 只展开一层，防指数放大


# ---------------------------------------------------------------------------
# 压缩包：多格式覆盖（tar 家族 / 单文件压缩流 / 缺解码器的降级）
# ---------------------------------------------------------------------------

def _tar_bytes(mode: str, entries: dict[str, bytes]) -> bytes:
    """构造一个 tar（可带 gz/bz2/xz 压缩层）。"""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode=mode) as tf:
        for name, data in entries.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


@pytest.mark.parametrize("name,mode", [
    ("讲义.tar", "w"),
    ("讲义.tar.gz", "w:gz"),
    ("讲义.tar.bz2", "w:bz2"),
    ("讲义.tar.xz", "w:xz"),
    ("讲义.tgz", "w:gz"),
    ("讲义.tbz2", "w:bz2"),
    ("讲义.txz", "w:xz"),
])
def test_tar_family_expands_entries(tmp_path, name, mode):
    """tar / tar.gz / tar.bz2 / tar.xz：包内文本都要抽出来（Windows 之外的常见打包）。"""
    path = write(tmp_path, name, _tar_bytes(mode, {
        "课程/readme.txt": "包内说明：考试范围见附件".encode(),
        "课程/成绩.csv": "学号,分数\n1,92\n".encode(),
    }))
    result = ingest_files.extract_file(path)
    assert "包内说明" in result.text
    assert "学号" in result.text
    assert "[包内文件] 课程/readme.txt" in result.text    # 保留来源，便于核对


@pytest.mark.parametrize("name,maker", [
    ("笔记.txt.gz", gzip.compress),
    ("笔记.txt.bz2", bz2.compress),
    ("笔记.txt.xz", lzma.compress),
])
def test_single_stream_compression_is_decompressed(tmp_path, name, maker):
    """单文件压缩流（gzip/bzip2/xz）：解压后按真实内容抽正文，不是「压缩包损坏」。"""
    path = write(tmp_path, name, maker("第三章 进程调度".encode()))
    result = ingest_files.extract_file(path)
    assert "进程调度" in result.text
    assert not result.warnings


def test_gzip_wrapping_document(tmp_path):
    """`报告.docx.gz` 这种「压缩一个文档」的写法要解出 Word 正文。"""
    from docx import Document

    doc = Document()
    doc.add_paragraph("压缩包里的实验报告：2026-12-10 前提交")
    buf = io.BytesIO()
    doc.save(buf)
    path = write(tmp_path, "报告.docx.gz", gzip.compress(buf.getvalue()))
    result = ingest_files.extract_file(path)
    assert "实验报告" in result.text


def test_nested_archive_only_one_level(tmp_path):
    """压缩包套压缩包只展开一层；但包内的「单文件压缩流」值得解（不是指数放大）。"""
    path = write(tmp_path, "混合.tar", _tar_bytes("w", {
        "内层.tar.gz": _tar_bytes("w:gz", {"深层.txt": "深层内容不该被展开".encode()}),
        "笔记.txt.gz": gzip.compress("嵌套压缩流里的正文".encode()),
    }))
    result = ingest_files.extract_file(path)
    assert "嵌套压缩流里的正文" in result.text
    assert "深层内容不该被展开" not in result.text
    assert any("嵌套压缩包" in w for w in result.warnings)


def test_archive_skips_os_metadata_junk(tmp_path):
    """macOS/Windows 的元数据条目（__MACOSX、._*）不该混进正文。"""
    path = write(tmp_path, "系统打的.tar", _tar_bytes("w", {
        "._讲义.txt": b"\x00\x00\x01Mac OS X        ",
        ".DS_Store": b"\x00\x00\x00\x01Bud1",
        "__MACOSX/讲义.txt": b"junk",
        "讲义.txt": "真正的讲义正文".encode(),
    }))
    text = ingest_files.extract_file(path).text
    assert "真正的讲义正文" in text
    assert "Mac OS X" not in text
    assert "__MACOSX" not in text


def test_tar_guard_limits_apply(tmp_path):
    """tar 也要守三重上限：超大条目跳过并说明，不能整包读进内存。"""
    path = write(tmp_path, "大包.tar", _tar_bytes("w", {
        "正常.txt": "包内正常文本".encode(),
        "巨无霸.txt": b"x" * (8 * 1024 * 1024 + 10),
    }))
    result = ingest_files.extract_file(path)
    assert "包内正常文本" in result.text
    assert any("超过单文件上限" in w for w in result.warnings)


def test_archive_without_decoder_gets_actionable_hint(tmp_path):
    """7z/rar 本机解不开：给「先解压再上传」的动作，而不是「无法识别的文件」。"""
    for name, magic in (("资料.7z", b"7z\xbc\xaf\x27\x1c"), ("资料.rar", b"Rar!\x1a\x07\x00")):
        info = formats.detect_format(name, data=magic + b"\x00" * 64)
        assert info.group == "archive", f"{name} 应被认成压缩包"
        assert info.supported is False
        result = ingest_files.extract_file(write(tmp_path, name, magic + b"\x00" * 64))
        assert result.text == ""
        warning = " ".join(result.warnings)
        assert "解压" in warning
        assert "无法识别" not in warning


@pytest.mark.parametrize("name,data", [
    ("坏.gz", b"\x1f\x8b\x08\x00" + b"\x00" * 40),
    ("坏.bz2", b"BZh9" + b"\x00" * 40),
    ("坏.xz", b"\xfd7zXZ\x00" + b"\x00" * 40),
    ("坏.zip", b"PK\x03\x04" + b"\x00" * 40),
])
def test_corrupt_archive_says_damaged(tmp_path, name, data):
    """坏压缩包要说「可能已损坏」，而不是笼统的「解析失败」或 500。

    gzip 的 CRC/块结构错误抛的是 `zlib.error`（不是 OSError），这里顺带守住
    解压异常集合别漏项——漏一个用户看到的就是「解析失败」。
    """
    result = ingest_files.extract_file(write(tmp_path, name, data))
    assert result.text == ""
    warning = " ".join(result.warnings)
    assert "损坏" in warning, warning
    assert "解析失败" not in warning


# ---------------------------------------------------------------------------
# 旧版 OLE2 抓取
# ---------------------------------------------------------------------------

def test_ole_scrape_utf16_and_ascii(tmp_path):
    """旧版 .doc/.ppt：UTF-16LE 中文与单字节 ASCII 都要抓到，并提示格式局限。"""
    body = ("第一章 操作系统概述".encode("utf-16-le") + b"\x00" * 8
            + b"This is the course outline for operating systems." + b"\x00" * 4)
    data = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 512
    data += "WordDocument".encode("utf-16-le") + b"\x00" * 64 + body
    path = write(tmp_path, "老文档.doc", data)
    result = ingest_files.extract_file(path)
    assert "第一章 操作系统概述" in result.text
    assert any("旧版" in w for w in result.warnings)


def test_ole_scrape_empty_binary(tmp_path):
    data = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 2048
    path = write(tmp_path, "空空.doc", data)
    result = ingest_files.extract_file(path)
    assert result.text == ""
    assert result.warnings


# ---------------------------------------------------------------------------
# 扫描版 PDF 的 OCR 兜底接线
# ---------------------------------------------------------------------------

def test_scanned_pdf_uses_ocr_when_available(tmp_path, monkeypatch):
    """无文本层 PDF：OCR 可用时把识别结果并入正文，并标明来源。"""
    from app.infra.ingest import ocr

    path = write(tmp_path, "扫描件.pdf", _empty_pdf_bytes())
    monkeypatch.setattr(ocr, "ocr_available", lambda: True)
    monkeypatch.setattr(ocr, "ocr_pdf", lambda p, **kw: "第一章 扫描出来的正文")
    result = ingest_files.extract_file(path)
    assert "扫描出来的正文" in result.text
    assert result.needs_ocr is False
    assert any("OCR" in w for w in result.warnings)


def test_scanned_pdf_without_ocr_keeps_hint(tmp_path, monkeypatch):
    """OCR 不可用：保留 needs_ocr 与「改用文字版」的中文提示。"""
    from app.infra.ingest import ocr

    path = write(tmp_path, "扫描件.pdf", _empty_pdf_bytes())
    monkeypatch.setattr(ocr, "ocr_available", lambda: False)
    result = ingest_files.extract_file(path)
    assert result.needs_ocr is True
    assert any("OCR" in w for w in result.warnings)


def test_pdf_extraction_never_raises_on_garbage(tmp_path):
    """损坏 PDF：返回空文本 + 提示，不抛异常。"""
    path = write(tmp_path, "坏.pdf", b"%PDF-1.4\nthis is not a real pdf body")
    result = ingest_files.extract_file(path)
    assert isinstance(result, ExtractedFile)
    assert result.text == ""
    assert result.warnings


# ---------------------------------------------------------------------------
# 注册表与分派一致性
# ---------------------------------------------------------------------------

def test_every_supported_kind_has_handler():
    """登记表里声明 supported 的 kind 必须都有抽取器（否则用户会遇到空结果）。"""
    from app.infra.ingest.formats import FORMATS, KIND_IMAGE, KIND_ODF

    special = {KIND_IMAGE, KIND_ODF}
    for spec in FORMATS.values():
        if not spec.supported or spec.kind in special:
            continue
        if spec.kind == "media":
            continue
        assert spec.kind in ingest_files.KIND_HANDLERS, f"{spec.ext} 缺少抽取器"


def test_ext_handlers_cover_registered_extensions():
    from app.infra.ingest.formats import FORMATS

    for ext, spec in FORMATS.items():
        if spec.kind in ("image", "odf", "media"):
            continue
        if not spec.supported:
            # 本机做不了的格式（7z/rar/dmg/zst、音视频）不挂 handler，
            # 由 files._unsupported 统一给「先解压/先转写」的可操作指引
            continue
        assert ext in ingest_files.EXT_HANDLERS, f"{ext} 未挂 handler"


def test_office_odf_table_repeat_clamped():
    """ODF 重复列/行必须夹紧：恶意文件会写 1000000 次重复。"""
    from xml.etree import ElementTree

    xml = (
        '<table:table xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
        'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" table:name="T">'
        '<table:table-row table:number-rows-repeated="1000000">'
        '<table:table-cell table:number-columns-repeated="100000">'
        "<text:p>x</text:p></table:table-cell></table:table-row></table:table>"
    )
    rendered = office._odf_table(ElementTree.fromstring(xml), max_rows=5, max_cols=4)
    lines = rendered.split("\n")
    assert len(lines) <= 6
    assert all(len(line.split(" | ")) <= 4 for line in lines)


def _empty_pdf_bytes() -> bytes:
    """生成一张「没有文本层」的 PDF（只有空白页），用于扫描件降级路径。

    PyMuPDF 必须经 `office._pymupdf()` 导入（告警升级为错误的配置下裸 import
    会让进程段错误，详见 `office._pymupdf` 的说明）。
    """
    from app.infra.ingest.office import _pymupdf

    pymupdf = _pymupdf()
    if pymupdf is None:  # pragma: no cover - 缺依赖时跳过
        pytest.skip("PyMuPDF unavailable")
    doc = pymupdf.open()
    doc.new_page()
    data = doc.tobytes()
    doc.close()
    return data
