"""课表导入：新增来源格式（PDF / 网页 HTML / Word(.docx) / ODS）+ 扩展名与内容不一致。

全部用**真实文件**，不 mock 解析器（mock 只能证明「我以为的输入长这样」）：

- PDF 用 PyMuPDF 现场生成：画表格线的（走 `find_tables`）与只有文字的（走词框坐标重建）；
- HTML 手工拼 `<table>`，含 colspan/rowspan 合并单元格（教务系统导出网页的常态）；
- DOCX 用 python-docx 建表；ODS 用 zipfile 手工拼最小包（mimetype + manifest + content.xml）；
- 扫描版 PDF 用 Pillow 生成图片再插进 PDF（没有文字层），OCR 引擎用替身。

PyMuPDF 一律通过 `app.courseimport_sheet._pymupdf()` 取用：pytest.ini 把
DeprecationWarning 当错误，而它的 SWIG 扩展在 import 时会发这种警告——异常从 C
扩展初始化里抛出会直接把测试进程打成段错误，所以不能在用例里裸 `import pymupdf`。
"""
from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

import pytest

from app.domain.courseimport import ImportParseError
from app.domain.courseimport_sheet import (
    MAX_GRID_COLS,
    _grid_from_docx,
    _grid_from_html,
    _grid_from_ods,
    _grid_from_pdf,
    _pymupdf,
    _unmerge_label_column,
    detect_kind,
    parse_grid,
    read_grid,
)
from app.infra.ingest import formats

# 典型教务系统二维课表：第 1 行学年学期，第 2 行星期表头，左侧节次 + 时间标签
GRID = [
    ["2025-2026学年第一学期 学生个人课表", "", "", "", ""],
    ["节次", "时间", "周一", "周二", "周三"],
    ["第1-2节", "08:00-09:40", "高等数学\n李四\n教1-202\n1-16周", "",
     "计算机组成原理\n王五\n教3-101\n1-16周"],
    ["第3-4节", "10:00-11:40", "", "数据结构\n赵六\n实验楼A201\n1-16周(单)", ""],
    ["备注：以教务系统为准", "", "", "", ""],
]
ALL_NAMES = {"高等数学", "计算机组成原理", "数据结构"}


def by_name(parsed) -> dict:
    return {e["name"]: e for e in parsed["entries"]}


def post_file(client, name: str, data: bytes, content_type="application/octet-stream"):
    return client.post(
        "/api/courses/import/preview",
        files={"file": (name, data, content_type)},
    )


# ---------------------------------------------------------------------------
# 真实文件生成（PDF / DOCX / ODS / HTML）
# ---------------------------------------------------------------------------

def draw_table_pdf(rows, *, ruled: bool, colw: int) -> bytes:
    """把二维课表按坐标画成 PDF：ruled=True 画表格线，False 只有文字。"""
    pymupdf = _pymupdf()
    rowh, left, top = 90, 60, 80
    doc = pymupdf.open()
    page = doc.new_page(width=left * 2 + colw * len(rows[0]),
                        height=top * 2 + rowh * len(rows))
    for r, row in enumerate(rows):
        for c, cell in enumerate(row):
            rect = pymupdf.Rect(left + c * colw, top + r * rowh,
                                left + (c + 1) * colw, top + (r + 1) * rowh)
            if ruled:
                page.draw_rect(rect, color=(0, 0, 0), width=0.7)
            for k, line in enumerate(cell.split("\n")):
                page.insert_text((rect.x0 + 6, rect.y0 + 18 + k * 13), line,
                                 fontname="china-s", fontsize=10)
    data = doc.tobytes()
    doc.close()
    return data


def pdf_bytes(*, ruled: bool) -> bytes:
    """生成完整课表 PDF：ruled=True 走表格识别，False 走词框坐标重建。"""
    pymupdf = _pymupdf()
    # 列宽按最长的一串文字算，再给下一列留 100pt 空隙：文本跨过列线时 find_tables
    # 会把它切到隔壁列，而纯文字版靠词框聚类，标题离邻列太近（<30pt）会把两列并成一列。
    # 页面因此用自定义尺寸——A4 宽度放不下这么宽的 5 列。
    colw = max(170, int(pymupdf.get_text_length(
        GRID[0][0], fontname="china-s", fontsize=10)) + 100)
    return draw_table_pdf(GRID, ruled=ruled, colw=colw)


#: 单列行标签（节次 + 时间写在同一格、每格多行）的课表：教务系统 PDF 常见排版
NARROW_GRID = [
    ["", "周一", "周二", "周三"],
    ["第1-2节\n08:00-09:40", "高等数学\n李四\n教1-202\n1-16周", "",
     "计算机组成原理\n王五\n教3-101\n1-16周"],
    ["第3-4节\n10:00-11:40", "", "数据结构\n赵六\n实验楼A201\n1-16周(单)", ""],
]


def narrow_text_pdf_bytes() -> bytes:
    """窄列 + 无表格线的课表 PDF：宽宽的行标签框会被聚类并进「周一」列。

    这正是 `_unmerge_label_column` 要补救的版式（列宽 130pt、标签文字宽约 110pt）。
    """
    return draw_table_pdf(NARROW_GRID, ruled=False, colw=130)


def scanned_pdf_bytes() -> bytes:
    """生成没有文字层的「扫描版」PDF：整页就是一张图片。"""
    from PIL import Image

    pymupdf = _pymupdf()
    buf = io.BytesIO()
    Image.new("RGB", (600, 800), "white").save(buf, format="PNG")
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_image(page.rect, stream=buf.getvalue())
    data = doc.tobytes()
    doc.close()
    return data


def docx_bytes(grid) -> bytes:
    """python-docx 建表写出的真实 .docx。"""
    from docx import Document

    document = Document()
    table = document.add_table(rows=len(grid), cols=len(grid[0]))
    for r, row in enumerate(grid):
        for c, value in enumerate(row):
            table.cell(r, c).text = value
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


def docx_text_only_bytes(lines) -> bytes:
    """只有段落文字的 .docx（老师常发的纯文本课表通知）。"""
    from docx import Document

    document = Document()
    for line in lines:
        document.add_paragraph(line)
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


ODS_MIMETYPE = "application/vnd.oasis.opendocument.spreadsheet"
ODS_MANIFEST = """<?xml version="1.0" encoding="UTF-8"?>
<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"
                   manifest:version="1.2">
 <manifest:file-entry manifest:full-path="/" manifest:media-type="%s"/>
 <manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>
</manifest:manifest>""" % ODS_MIMETYPE
ODS_HEAD = """<?xml version="1.0" encoding="UTF-8"?>
<office:document-content
    xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
    xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0"
    xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" office:version="1.2">
 <office:body><office:spreadsheet><table:table table:name="课表">"""
ODS_TAIL = "</table:table></office:spreadsheet></office:body></office:document-content>"


def ods_cell(text: str = "", *, repeat: int = 1, row_repeat: int = 1,
             paragraphs: int = 1) -> str:
    """一个 table:table-cell（含重复次数与多段文本）。"""
    attrs = ""
    if repeat != 1:
        attrs += f' table:number-columns-repeated="{repeat}"'
    if row_repeat != 1:
        attrs += f' table:number-rows-repeated="{row_repeat}"'
    body = "".join(f"<text:p>{text}</text:p>" for _ in range(paragraphs))
    return f"<table:table-cell{attrs}>{body}</table:table-cell>"


def ods_bytes(rows, *, pad_columns: int = 0) -> bytes:
    """手工拼最小 ODS：mimetype + META-INF/manifest.xml + content.xml。"""
    xml = ODS_HEAD
    for row in rows:
        cells = "".join(ods_cell(value) for value in row)
        if pad_columns:
            # LibreOffice 导出常见：每行尾部用 number-columns-repeated 补空格
            cells += ods_cell("", repeat=pad_columns)
        xml += f"<table:table-row>{cells}</table:table-row>"
    xml += ODS_TAIL
    return zip_bytes({
        "mimetype": ODS_MIMETYPE,
        "META-INF/manifest.xml": ODS_MANIFEST,
        "content.xml": xml,
    })


def zip_bytes(entries: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, value in entries.items():
            zf.writestr(name, value)
    return buf.getvalue()


#: 带合并单元格的课表网页（教务系统「导出网页」的常态：标题 colspan、节次 rowspan）
MERGED_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>学生个人课表</title>
<style>table{border-collapse:collapse}</style><script>var x=1;</script></head>
<body>
<table border="1">
  <tr><td colspan="5">2025-2026学年第一学期 学生个人课表</td></tr>
  <tr><td>节次</td><td>时间</td><td>周一</td><td>周二</td><td>周三</td></tr>
  <tr><td rowspan="2">第1-2节</td><td rowspan="2">08:00-09:40</td>
      <td>高等数学<br>李四<br>教1-202<br>1-16周</td><td></td>
      <td>计算机组成原理<br>王五<br>教3-101<br>1-16周</td></tr>
  <tr><td></td><td></td><td></td></tr>
  <tr><td>第3-4节</td><td>10:00-11:40</td><td></td>
      <td>数据结构<br>赵六<br>实验楼A201<br>1-16周(单)</td><td></td></tr>
  <tr><td colspan="5">备注：以教务系统为准</td></tr>
</table>
</body></html>"""


def html_bytes(html: str = MERGED_HTML) -> bytes:
    return html.encode("utf-8")


# ---------------------------------------------------------------------------
# 格式嗅探
# ---------------------------------------------------------------------------

def test_detect_kind_sniffs_new_formats():
    """按内容判断，不看扩展名：pdf / html / docx / ods 各归各位。"""
    assert detect_kind(pdf_bytes(ruled=True)) == "pdf"
    assert detect_kind(html_bytes()) == "html"
    assert detect_kind(docx_bytes(GRID)) == "docx"
    assert detect_kind(ods_bytes(GRID)) == "ods"
    # 既有语义不变：纯文本仍是 csv，认不出的文本也落回 csv
    assert detect_kind("周一,周二\n高等数学,".encode("utf-8")) == "csv"
    assert detect_kind(b"just some plain text") == "csv"


def test_image_ext_covers_the_new_magic_types():
    """图片判定只认图片扩展名：md/ics/纯文本不能被当成截图，新的位图格式要认。"""
    for ext in (".md", ".ics", ".txt", ".csv"):
        assert formats.is_image_ext(ext) is False
    for ext in (".png", ".jpg", ".gif", ".tif", ".tiff", ".avif", ".heic"):
        assert formats.is_image_ext(ext) is True


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

def test_grid_from_pdf_ruled_table():
    """有表格线的 PDF：find_tables 直接给单元格，含节次/时间与课程名。"""
    grid = _grid_from_pdf(pdf_bytes(ruled=True))
    assert grid[1] == ["节次", "时间", "周一", "周二", "周三"]
    assert "高等数学" in grid[2][2] and "计算机组成原理" in grid[2][4]
    assert "数据结构" in grid[3][3]
    parsed = parse_grid(grid, fmt="pdf")
    assert parsed["format"] == "pdf"
    assert parsed["semester"] == "2025-2026-1"
    assert set(by_name(parsed)) == ALL_NAMES


def test_grid_from_pdf_text_only_uses_word_boxes():
    """没有表格线的 PDF：词框坐标聚类还原行列（教学系统排版稿常见）。"""
    grid = _grid_from_pdf(pdf_bytes(ruled=False))
    assert grid[1] == ["节次", "时间", "周一", "周二", "周三"]
    assert set(by_name(parse_grid(grid, fmt="pdf"))) == ALL_NAMES


def test_unmerge_label_column_repairs_merged_left_column():
    """纯文字 PDF 的补救：左标签列被并进「周一」列时要拆回两列。

    这种网格如果直接交给 parse_grid，整列会被当成行标签丢掉，里面的课程名一起消失。
    """
    merged = [
        ["周一", "周二", "周三"],
        ["第1-2节 08:00-09:40 李四 高等数学 1-16周", "", "王五 计算机组成原理"],
        ["第3-4节 10:00-11:40", "赵六 数据结构", ""],
    ]
    grid = _unmerge_label_column(merged)
    assert grid[0] == ["", "周一", "周二", "周三"]
    assert grid[1][:2] == ["第1-2节 08:00-09:40", "李四 高等数学 1-16周"]
    assert grid[2][:2] == ["第3-4节 10:00-11:40", ""]
    entries = by_name(parse_grid(grid))
    assert set(entries) == {"高等数学", "计算机组成原理", "数据结构"}
    assert entries["高等数学"]["slots"][0]["weekday"] == 1


def test_unmerge_label_column_leaves_normal_grid_alone():
    """正常网格（左标签列本来就在）不能被拆坏。"""
    grid = [["", "周一", "周二"], ["第1-2节", "高等数学", ""], ["第3-4节", "", "英语"]]
    assert _unmerge_label_column(grid) == grid
    # 以周次开头的课程格（1-16周 高等数学）也不能被误当成行标签
    weeks_first = [["", "周一", "周二"], ["08:00-09:40", "1-16周 高等数学", ""],
                   ["10:00-11:40", "", "英语"]]
    assert _unmerge_label_column(weeks_first) == weeks_first


def test_grid_from_pdf_narrow_columns_repairs_merged_label_column():
    """窄列纯文字 PDF：标签列被并进「周一」列后，拆回来仍要认出全部课程。"""
    grid = _grid_from_pdf(narrow_text_pdf_bytes())
    assert grid[0] == ["", "周一", "周二", "周三"]
    entries = by_name(parse_grid(grid, fmt="pdf"))
    assert set(entries) == ALL_NAMES
    assert entries["高等数学"]["slots"][0]["weekday"] == 1
    assert entries["数据结构"]["slots"][0]["weekday"] == 2


def test_grid_from_pdf_corrupt_file_gives_actionable_error():
    with pytest.raises(ImportParseError, match="无法识别的课表文件"):
        _grid_from_pdf(b"%PDF-1.4 broken")
    with pytest.raises(ImportParseError, match="另存为|导入"):
        read_grid(b"%PDF-1.4 broken")


# ---------------------------------------------------------------------------
# 网页 HTML
# ---------------------------------------------------------------------------

def test_grid_from_html_expands_merged_cells():
    """rowspan/colspan 必须展开：合并格覆盖到的每一格都要有内容。"""
    grid = _grid_from_html(html_bytes())
    assert grid[1] == ["节次", "时间", "周一", "周二", "周三"]
    # 标题 colspan=5 → 五列都写上同一份文本
    assert grid[0] == ["2025-2026学年第一学期 学生个人课表"] * 5
    # 节次 rowspan=2 → 两行都有「第1-2节」，第二行也不是空行
    assert grid[2][0] == grid[3][0] == "第1-2节"
    assert grid[2][1] == grid[3][1] == "08:00-09:40"
    # <br> 转成换行，课程字段才分得开
    assert grid[2][2].splitlines() == ["高等数学", "李四", "教1-202", "1-16周"]
    assert set(by_name(parse_grid(grid, fmt="html"))) == ALL_NAMES


def test_grid_from_html_stdlib_fallback(monkeypatch):
    """lxml 缺失/失败时回退标准库 html.parser：合并单元格同样要展开。"""
    monkeypatch.setitem(sys.modules, "lxml", None)  # from lxml import html → ImportError

    def boom(*_args, **_kwargs):  # pragma: no cover - 只用于确认回退路径
        raise AssertionError("lxml 不可用时不应被调用")

    monkeypatch.setattr("app.domain.courseimport_sheet._html_tables_lxml", boom)
    grid = _grid_from_html(html_bytes())
    assert grid[1] == ["节次", "时间", "周一", "周二", "周三"]
    assert grid[2][0] == "第1-2节" and "高等数学" in grid[2][2]
    assert set(by_name(parse_grid(grid, fmt="html"))) == ALL_NAMES


def test_grid_from_html_without_table_gives_actionable_error():
    with pytest.raises(ImportParseError, match="表格"):
        _grid_from_html("<html><body><p>高等数学 周一 08:00</p></body></html>".encode("utf-8"))


# ---------------------------------------------------------------------------
# Word(.docx)
# ---------------------------------------------------------------------------

def test_grid_from_docx_table():
    grid = _grid_from_docx(docx_bytes(GRID))
    assert grid[1] == ["节次", "时间", "周一", "周二", "周三"]
    assert "高等数学" in grid[2][2]
    parsed = parse_grid(grid, fmt="word")
    assert parsed["format"] == "word"
    entries = by_name(parsed)
    assert set(entries) == ALL_NAMES
    assert entries["数据结构"]["teacher"] == "赵六"
    assert entries["数据结构"]["slots"][0]["weeks"] == [1, 3, 5, 7, 9, 11, 13, 15]


def test_grid_from_docx_paragraphs_without_table():
    """整篇没有表格：按段落折成单列网格，至少不丢文字。"""
    data = docx_text_only_bytes(["2025-2026学年第一学期课表", "高等数学 周一 08:00-09:40"])
    grid = _grid_from_docx(data)
    assert [len(row) for row in grid] == [1, 1]
    assert grid[0][0].startswith("2025-2026")
    # 单列网格没有星期表头：报错要指向可操作的下一步，而不是抛底层异常
    with pytest.raises(ImportParseError, match="周一"):
        parse_grid(grid, fmt="word")


# ---------------------------------------------------------------------------
# ODS
# ---------------------------------------------------------------------------

def test_grid_from_ods_reads_content_xml():
    data = ods_bytes(GRID, pad_columns=40)  # 教务系统常见：每行尾部补一堆空格
    assert formats.sniff(data) == ".ods"
    grid = _grid_from_ods(data)
    assert grid[1] == ["节次", "时间", "周一", "周二", "周三"]
    # 补齐的 40 个空列已被裁掉：宽度就是表头那 5 列
    assert [len(row) for row in grid] == [5] * len(grid)
    assert set(by_name(parse_grid(grid, fmt="ods"))) == ALL_NAMES


def test_grid_from_ods_repeated_cells_and_rows():
    """number-columns-repeated / number-rows-repeated 都要展开。"""
    xml = ODS_HEAD
    xml += "<table:table-row>" + ods_cell("节次") + ods_cell("周一", repeat=3) + "</table:table-row>"
    xml += "<table:table-row>" + ods_cell("第1-2节", row_repeat=2) + ods_cell("高等数学") + "</table:table-row>"
    xml += ODS_TAIL
    data = zip_bytes({"mimetype": ODS_MIMETYPE, "META-INF/manifest.xml": ODS_MANIFEST,
                      "content.xml": xml})
    grid = _grid_from_ods(data)
    assert grid[0] == ["节次", "周一", "周一", "周一"]      # 列重复
    assert [row[:2] for row in grid[1:]] == [["第1-2节", "高等数学"], ["第1-2节", "高等数学"]]  # 行重复


def test_grid_from_ods_clamps_insane_repeat():
    """恶意文件写 number-columns-repeated=1000000：必须夹紧，不能撑爆内存。"""
    xml = ODS_HEAD
    xml += "<table:table-row>" + ods_cell("恶意", repeat=1000000) + "</table:table-row>"
    xml += ODS_TAIL
    data = zip_bytes({"mimetype": ODS_MIMETYPE, "META-INF/manifest.xml": ODS_MANIFEST,
                      "content.xml": xml})
    grid = _grid_from_ods(data)
    assert len(grid) == 1 and len(grid[0]) <= MAX_GRID_COLS


def test_grid_from_ods_without_content_xml_gives_actionable_error():
    data = zip_bytes({"mimetype": ODS_MIMETYPE})
    with pytest.raises(ImportParseError, match="另存为|损坏"):
        _grid_from_ods(data)


# ---------------------------------------------------------------------------
# 接口：新增格式的预览
# ---------------------------------------------------------------------------

def test_preview_pdf_text_layer(client):
    r = post_file(client, "学生个人课表.pdf", pdf_bytes(ruled=True), "application/pdf")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["format"] == "pdf" and body["format_label"] == "PDF 课表"
    assert body["ai_assisted"] is False and body["semester"] == "2025-2026-1"
    entries = by_name(body)
    assert set(entries) == ALL_NAMES
    math = entries["高等数学"]
    assert math["teacher"] == "李四" and math["location"] == "教1-202"
    assert math["slots"] == [{
        "weekday": 1, "start": "08:00", "end": "09:40",
        "weeks": list(range(1, 17)), "location": "教1-202", "teacher": "李四",
    }]
    assert entries["数据结构"]["slots"][0]["weekday"] == 2
    assert entries["计算机组成原理"]["slots"][0]["weekday"] == 3


def test_preview_pdf_without_table_lines(client):
    """纯文字版 PDF 也要能导入（词框坐标重建），且格式仍标成 PDF 课表。"""
    r = post_file(client, "课表.pdf", pdf_bytes(ruled=False), "application/pdf")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["format"] == "pdf" and body["format_label"] == "PDF 课表"
    assert set(by_name(body)) == ALL_NAMES


def test_preview_html_with_merged_cells(client):
    r = post_file(client, "课表.html", html_bytes(), "text/html")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["format"] == "html" and body["format_label"] == "网页课表"
    entries = by_name(body)
    assert set(entries) == ALL_NAMES
    assert entries["高等数学"]["slots"][0]["weekday"] == 1
    assert entries["数据结构"]["slots"][0]["weeks"] == [1, 3, 5, 7, 9, 11, 13, 15]


def test_preview_docx_file(client):
    r = post_file(
        client, "课表.docx", docx_bytes(GRID),
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["format"] == "word" and body["format_label"] == "Word 课表"
    assert set(by_name(body)) == ALL_NAMES


def test_preview_ods_file(client):
    r = post_file(client, "课表.ods", ods_bytes(GRID, pad_columns=40))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["format"] == "ods" and body["format_label"] == "ODS 课表"
    assert set(by_name(body)) == ALL_NAMES


def test_preview_json_file(client):
    payload = ('{"semester":"2025-2026-1","courses":[{"name":"线性代数","teacher":"王五",'
               '"slots":[{"weekday":2,"start":"08:00","end":"09:40","weeks":"1-8"}]}]}')
    r = post_file(client, "课表.json", payload.encode("utf-8"), "application/json")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["format"] == "json" and body["format_label"] == "JSON 课表"
    assert [e["name"] for e in body["entries"]] == ["线性代数"]


def test_preview_extension_mismatch_uses_content(client):
    """把 .docx 命名成 .xls：按内容解析（扩展名不可信）。"""
    r = post_file(client, "课表.xls", docx_bytes(GRID))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["format"] == "word" and body["format_label"] == "Word 课表"
    assert set(by_name(body)) == ALL_NAMES
    # 反向：网页改名成 .xlsx 也按内容走网页解析
    r2 = post_file(client, "课表.xlsx", html_bytes())
    assert r2.status_code == 200, r2.text
    assert r2.json()["format"] == "html"


def test_preview_corrupt_pdf_gives_actionable_error(client):
    r = post_file(client, "课表.pdf", b"%PDF-1.4 broken")
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert "课表解析失败" in detail and ("损坏" in detail or "另存为" in detail)


def test_preview_empty_ods_gives_actionable_error(client):
    """只有 mimetype、没有 content.xml 的空 ODS：422 + 中文可操作提示。"""
    r = post_file(client, "课表.ods", zip_bytes({"mimetype": ODS_MIMETYPE}))
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert "课表解析失败" in detail and ("另存为" in detail or "损坏" in detail)


def test_preview_html_without_table_gives_actionable_error(client):
    r = post_file(client, "课表.html", html_bytes("<html><body><p>没有表格</p></body></html>"))
    assert r.status_code == 422
    assert "表格" in r.json()["detail"]


def test_preview_legacy_doc_gives_save_as_hint(client):
    """老式 .doc（OLE2 + WordDocument 流）：提示另存为 .docx，而不是笼统的识别失败。"""
    doc = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 32 + "WordDocument".encode("utf-16-le")
    r = post_file(client, "课表.doc", doc)
    assert r.status_code == 422
    assert ".docx" in r.json()["detail"]


# ---------------------------------------------------------------------------
# 接口：图片（新增 GIF/TIFF/AVIF/HEIC 魔数）与扫描版 PDF 的 OCR 兜底
# ---------------------------------------------------------------------------

OCR_ROWS = [
    ["", "周一", "周三"],
    ["08:00-09:40", "高等数学\n李四\n教1-202\n1-16周", ""],
    ["10:00-11:40", "", "计算机组成原理\n王五\n教3-101\n1-16周(单)"],
]


def stub_ocr(monkeypatch, *, rows=None, text="x", llm=None):
    """把 OCR 引擎与 LLM 换成替身（测试不加载模型、不联网）。"""
    from app.api.routers import courses as courses_router

    monkeypatch.setattr(courses_router.ocr, "ocr_available", lambda: True)
    monkeypatch.setattr(
        courses_router, "ocr_grid",
        lambda path: {"rows": OCR_ROWS if rows is None else rows, "text": text, "boxes": 12},
    )
    if llm is not None:
        monkeypatch.setattr(courses_router, "llm_extract_source", lambda source: llm)


@pytest.mark.parametrize("name,data", [
    ("课表.gif", b"GIF89a" + b"\x00" * 64),
    ("课表.tif", b"II*\x00" + b"\x00" * 64),
    ("课表.avif", b"\x00\x00\x00\x20ftypavif" + b"\x00" * 64),
])
def test_preview_new_image_magic_uses_ocr(client, monkeypatch, name, data):
    """GIF/TIFF/AVIF 也要进 OCR 通道（以前只有 PNG/JPG/BMP/WEBP 的魔数）。"""
    stub_ocr(monkeypatch)
    r = post_file(client, name, data)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["format"] == "image" and body["format_label"] == "课表截图"
    assert by_name(body)["高等数学"]["teacher"] == "李四"


def test_preview_heic_without_decoder_gives_conversion_hint(client):
    """HEIC 本机没有解码器：要明确让人转成 JPG/PNG，而不是泛泛的识别失败。"""
    if formats.decoder_available(".heic"):  # pragma: no cover - 装了 pillow-heif 的环境
        pytest.skip("本机装了 HEIC 解码器")
    data = b"\x00\x00\x00\x18ftypheic" + b"\x00" * 64
    r = post_file(client, "IMG_1234.HEIC", data)
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert "HEIC" in detail and "JPG/PNG" in detail


def test_preview_scanned_pdf_uses_ocr_grid(client, monkeypatch):
    """没有文字层的 PDF：渲染成 PNG 走 OCR 通道，格式仍报 PDF。"""
    stub_ocr(monkeypatch)
    r = post_file(client, "扫描课表.pdf", scanned_pdf_bytes(), "application/pdf")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["format"] == "pdf" and body["format_label"] == "PDF 课表"
    assert body["ai_assisted"] is False
    entries = by_name(body)
    assert entries["高等数学"]["teacher"] == "李四"
    assert entries["计算机组成原理"]["slots"][0]["weeks"] == [1, 3, 5, 7, 9, 11, 13, 15]


def test_preview_scanned_pdf_ai_fallback(client, monkeypatch):
    """OCR 重建不出网格 → 各页原文交给 LLM 兜底，预览标 ai_assisted。"""
    stub_ocr(
        monkeypatch, rows=[["看不懂的版式"]], text="周一 高等数学 08:00-09:40",
        llm={
            "format": "image", "ai_assisted": True, "term_start_date": None, "semester": "",
            "entries": [{"name": "高等数学", "code": "", "teacher": "", "location": "",
                         "slots": [{"weekday": 1, "start": "08:00", "end": "09:40",
                                    "weeks": [], "location": "", "teacher": ""}]}],
        },
    )
    r = post_file(client, "扫描课表.pdf", scanned_pdf_bytes(), "application/pdf")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ai_assisted"] is True
    assert body["format"] == "pdf" and body["format_label"] == "PDF 课表（AI 辅助）"
    assert [e["name"] for e in body["entries"]] == ["高等数学"]


def test_preview_scanned_pdf_without_ocr_engine(client, monkeypatch):
    """OCR 不可用：给出「改用 .ics/Excel/CSV/文本版 PDF」这类可操作提示。"""
    from app.api.routers import courses as courses_router

    monkeypatch.setattr(courses_router.ocr, "ocr_available", lambda: False)
    r = post_file(client, "扫描课表.pdf", scanned_pdf_bytes(), "application/pdf")
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert "OCR" in detail and "文本版 PDF" in detail


def test_preview_pdf_cleans_up_temporary_images(client, monkeypatch):
    """扫描版 PDF 的临时 PNG 必须清掉（不许在临时目录里留垃圾）。"""
    import glob
    import tempfile

    stub_ocr(monkeypatch)
    before = set(glob.glob(str(Path(tempfile.gettempdir()) / "studyos-course-pdf-*")))
    assert post_file(client, "扫描课表.pdf", scanned_pdf_bytes(), "application/pdf").status_code == 200
    after = set(glob.glob(str(Path(tempfile.gettempdir()) / "studyos-course-pdf-*")))
    assert after == before


# ---------------------------------------------------------------------------
# format 取值
# ---------------------------------------------------------------------------

def test_parse_grid_reports_the_new_format_values():
    for fmt in ("pdf", "html", "word", "ods", "excel", "csv", "image"):
        assert parse_grid(GRID, fmt=fmt)["format"] == fmt


# ---------------------------------------------------------------------------
# 回归：表头比数据行挨得更近的 PDF（曾被行合并吃掉表头）
# ---------------------------------------------------------------------------

def tight_header_pdf_bytes() -> bytes:
    """表头与首行的间距**小于**节次行距的课表 PDF。

    教务系统导出的 PDF 常见这种排版（表头紧贴第一行）。此处画成
    「只有文字、无表格线」——走词框坐标重建这条最容易踩坑的路径：
    表头距首行 60pt、节次行距 100pt，纯按距离判定曾把「周一…周日」
    并进「第1-2节」那一行，星期列消失、整份课表解析失败。
    """
    pymupdf = _pymupdf()
    left, colw = 60, 170
    header_y, first_row_y, row_pitch = 60, 120, 100
    cells = [
        (0, header_y, "节次"), (1, header_y, "周一"), (2, header_y, "周二"), (3, header_y, "周三"),
        (0, first_row_y, "第1-2节"), (1, first_row_y, "高等数学"),
        (1, first_row_y + 14, "李四"), (1, first_row_y + 28, "教1-202"),
        (0, first_row_y + row_pitch, "第3-4节"), (2, first_row_y + row_pitch, "数据结构"),
    ]
    doc = pymupdf.open()
    page = doc.new_page(width=left * 2 + colw * 4, height=first_row_y + row_pitch + 160)
    for col, y, text in cells:
        page.insert_text((left + col * colw + 6, y), text, fontname="china-s", fontsize=10)
    data = doc.tobytes()
    doc.close()
    return data


def test_pdf_with_tight_header_still_finds_weekday_columns():
    """表头紧贴首行的 PDF 仍要解析出课程：表头行不得被行合并吞掉。"""
    grid = _grid_from_pdf(tight_header_pdf_bytes())
    header_rows = [row for row in grid if "周一" in row]
    assert header_rows, grid
    assert "周一" in header_rows[0] and "周二" in header_rows[0] and "周三" in header_rows[0]
    parsed = parse_grid(grid, fmt="pdf")
    assert {e["name"] for e in parsed["entries"]} == {"高等数学", "数据结构"}


def test_pdf_with_tight_header_through_api(client):
    """接口层同样要过（用户上传的就是这种文件）。"""
    r = post_file(client, "紧凑表头课表.pdf", tight_header_pdf_bytes(), "application/pdf")
    assert r.status_code == 200, r.text
    names = {e["name"] for e in r.json()["entries"]}
    assert names == {"高等数学", "数据结构"}
    assert r.json()["format"] == "pdf" and r.json()["format_label"] == "PDF 课表"
