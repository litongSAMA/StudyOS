"""导入课表解析：二维课表来源 → 课程条目。

支持把 **Excel(.xlsx/.xls) / CSV·TSV / ODS / PDF / 网页 HTML / Word(.docx)** 六类
文件与 **课表截图 OCR 网格** 统一读成 `list[list[str]]`，再走同一套语义解析。
教务系统（正方/强智等）与课表 App 导出的「二维课表」是同一个形状：
**一行 = 一个节次/时刻，一列 = 一个星期**，左上角若干行是表头（学年学期、
节次时间），第一列是节次或时间标签。本模块把这个网格还原成课程条目：

- 表头行：识别含「周一…周日」的行（或 mon…sun 写法），确定每个列对应的星期；
- 行标签：识别「第1-2节 / 1-2节 / 1、2 / 3-4」等节次写法（查标准 12 节时间表）
  与「08:00-09:40 / 8:00~9:40」等直接时刻写法；
- 单元格：一个格子可能是「高等数学\\n张三\\n教3-101\\n1-16周(单)」多行文本，
  也可能是「高等数学(1-16周)@教3-101」单行写法；括号里的周次、单双周、老师、
  地点就地抽取；
- 学期锚点：表头里的「2025-09-01 / 2025年9月1日」或「第1周(9月1日)」优先，
  否则 **term_start_date 为 None**（本模块无法知道真实开学日），由前端确认。

所有时间都是**本地墙上时间**（课表语义是「周一 08:00 上课」，与设备时区无关）。
"""
from __future__ import annotations

import csv
import io
import re
import shutil
import tempfile
import warnings
import zipfile
import xml.etree.ElementTree as ET
from datetime import date, datetime, time as dtime
from html.parser import HTMLParser
from pathlib import Path

from .courseimport import (
    MAX_ENTRIES,
    MAX_SLOTS,
    ImportParseError,
    _clean_name,
    _minutes,
    _parse_time,
    guess_semester,
    parse_weeks,
    period_end_time,
    period_to_time,
)
from app.infra.ingest import formats

MAX_GRID_ROWS = 200          # 网格规模护栏（防异常文件拖垮解析）
MAX_GRID_COLS = 30
MAX_CELL_CHARS = 300
MAX_HEADER_SCAN = 8          # 表头最多扫前 8 行
MAX_CELL_CHUNKS = 12         # 单元格最多切分出的候选课程块数
FOOTER_MARKERS = ("备注", "说明", "注：", "注:", "注1", "注2")

XLSX_MAGIC = b"PK\x03\x04"
XLS_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

# ---- 新增来源的规模护栏（每种来源都要能挡住异常/恶意文件）----
MAX_PDF_PAGES = 20           # PDF 只扫前 20 页：课表总在文件开头
MAX_PDF_TABLES = 20          # 单页最多检查的表格数
MAX_PDF_WORDS = 20_000       # 词框上限：坐标聚类是 O(n·组数)，异常 PDF 会拖死请求
MAX_PDF_OCR_PAGES = 10       # 扫描版 PDF 走 OCR 时的页数上限（一页 ~2 秒）
MAX_PDF_OCR_DPI = 160        # OCR 栅格化 DPI：150~200 之间，够认字又不至于慢
MAX_HTML_CHARS = 1_000_000   # 网页正文上限（上传本身已有 2MB 上限）
MAX_HTML_TABLES = 50
MAX_ODS_XML_BYTES = 8 * 1024 * 1024   # content.xml 解压后上限（防解压炸弹）
MAX_ODS_TABLES = 20          # .ods 最多检查的表数量
MAX_ODS_PARAGRAPHS = 8       # 单个 ODS 单元格最多取 8 段文本
MAX_DOCX_PARAGRAPHS = 500    # Word 没有表格时最多取 500 段
MAX_DOCX_TABLES = 50         # Word 最多检查的表格数
MAX_SPAN = MAX_GRID_ROWS     # rowspan/colspan 上限（恶意网页会写 999999）

# 学期信息（表头常见）：2025-2026学年第一学期 / 2025-2026学年 第2学期
_SEMESTER_RE = re.compile(
    r"(20\d{2})\s*[-—~–]\s*(20\d{2})"
    r"(?:\s*学年\s*第?\s*([一二三四1-4])\s*学期|\s*学年\s*学期\s*[:：]?\s*([12])|[-—~–]\s*([12])(?!\d))"
)
_SEMESTER_LOOSE_RE = re.compile(r"(20\d{2})\s*[-—~–]\s*(20\d{2})\s*学年")
# 开学日期：2025-09-01 / 2025/9/1 / 2025年9月1日 / 2025.9.1
_DATE_RE = re.compile(r"(20\d{2})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})")
# 第1周(9月1日) / 第1周 9.1（表头只给月日，年份从学年推）
_WEEK1_RE = re.compile(r"第\s*1\s*周[^\d]{0,8}?(\d{1,2})\s*[-/.月]\s*(\d{1,2})")
_EN_WEEKDAY = {
    "mon": 1, "monday": 1, "tue": 2, "tues": 2, "tuesday": 2, "wed": 3,
    "wednesday": 3, "thu": 4, "thur": 4, "thurs": 4, "thursday": 4, "fri": 5,
    "friday": 5, "sat": 6, "saturday": 6, "sun": 7, "sunday": 7,
}
_WEEKDAY_CN = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7,
               "天": 7, "1": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7}
# 行标签节次：第1-2节 / 1-2节 / 1、2 / 第3节 / 1 / 第一大节
_PERIOD_RE = re.compile(r"^(?:第)?\s*(\d{1,2})\s*(?:[-—~–、,，]|至|到|和)?\s*(\d{1,2})?\s*(?:节|课)?$")
# 行标签「大节」：第一大节 / 第3大节 / 大节一 / 大节 2（南理工等教务系统按大节分行）
_MAJOR_PERIOD_RE = re.compile(r"^(?:第)?\s*([一二三四五六七八九十\d]{1,3})\s*大\s*节$")
_MAJOR_CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
                 "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
#: 「第N大节」→ (首节, 末节)。国内高校课表把一天切成 5 个上午/下午/晚上大节，
#: 每节 2 小节；末节封顶 10（晚上 19:00-20:40）而不是 12，避免凭空造出 21:00 的课。
_MAJOR_PERIODS: dict[int, tuple[int, int]] = {
    1: (1, 2), 2: (3, 4), 3: (5, 6), 4: (7, 8), 5: (9, 10),
}
# 行标签时刻：08:00-09:40 / 8:00~9:40 / 08:00
_TIME_RE = re.compile(
    r"(\d{1,2}\s*[:：]\s*\d{2}(?:\s*[:：]\s*\d{2})?)"
    r"(?:\s*[-—~–至到]+\s*(\d{1,2}\s*[:：]\s*\d{2}(?:\s*[:：]\s*\d{2})?))?"
)
# 周次标记：1-16周 / 第1周 / 4-6,8-18[周] / 1-16周(单) / 1,3,5-8周
# 教务系统写法很杂，两端括号可有可无、方括号圆括号混用；中间用逗号串起多段周次
# （parse_weeks 认「4-6,8-18」），所以逗号也要吃进来，否则会漏掉后半段周次。
#
# 抠周次靠「找周字 + 纯字符判断」（见 _take_week_token），不靠一条大正则：
# CPython 3.14.4 的 re 一旦在一个起始位置匹配失败就**不再往后找**（`\d{1,2}周`
# 在「6[周]」上什么都不返回，尽管「6周」明明就在里面），本模块要同时跑在
# 3.12 与 3.14 上，凡是「周」后面可能跟括号的写法都不能交给 re 去扫。
_WEEK_DIGITS_RE = re.compile(r"\d{1,2}")
MAX_WEEK_LABEL = 40   # 行标签里的最大周次：超过它的一律当成房间号（教3-101）
# 教师行：「张新义(讲师)」「吴海平(副教授),钱彦(副教授)」——职称括注要能剥掉，
# 否则老师名会连职称一起入库，认教师/选课名的启发式也全都失准。
_TEACHER_TITLE = r"(?:教授|副教授|讲师|助教|助理研究员|副研究员|研究员|高级工程师|工程师|实验师|高级实验师|院士|博导|硕导|老师)"
_TEACHER_TITLE_RE = re.compile(r"[（(\[【]\s*" + _TEACHER_TITLE + r"\s*[)）\]】]")
_NAME_BRACKET_RE = re.compile(r"^[【\[（(]([^】\]）)]{1,40})[】\]）)]\s*(.*)$")
_ROOM_SUFFIX_RE = re.compile(
    r"(?:\s+|^)([\u4e00-\u9fff]{0,6}\d{0,2}[-—]?[A-Za-z]?\d{2,4}|[\u4e00-\u9fff]{1,6}楼[^\s]*)$"
)
_TEACHER_RE = re.compile(r"(?:教师|老师|任课教师)\s*[:：]\s*([^\s,，;；]{1,20})")
_LOCATION_RE = re.compile(
    r"(?:地点|教室|上课地点)\s*[:：]\s*([^\s,，;；]{1,30})|@\s*([^\s,，;；]{1,30})"
)
_FOOTNOTE_RE = re.compile(r"[（(]?\s*(?:注|备注)\s*\d*\s*[)）]?\s*$")


# ---------------------------------------------------------------------------
# 二进制来源 → 二维网格
# ---------------------------------------------------------------------------

class PdfUnreadableError(ImportParseError):
    """PDF 打不开（损坏/加密）：不是「没有文字层」，OCR 兜底也救不了，提示要另说。"""


#: 内容嗅探出的规范扩展名 → 课表解析 kind。
#: 认不出来的一律落回 "csv"（纯文本按 CSV 网格读，与既有语义一致）；
#: 认得出但读不成网格的格式（.doc/.ppt/.odt…）带上自己的 kind，由上层给「另存为」提示。
_SNIFF_KINDS = {
    ".xlsx": "xlsx", ".xls": "xls", ".ods": "ods", ".docx": "docx",
    ".pdf": "pdf", ".html": "html", ".htm": "html", ".xhtml": "html",
    ".doc": "doc", ".ppt": "ppt", ".pptx": "pptx", ".odt": "odt", ".zip": "zip",
}

#: 能直接当 CSV/TSV 文本读的规范扩展名（其余被嗅探认出的格式一律按二进制处理）
_TEXTUAL_EXTS = frozenset({
    "", ".csv", ".tsv", ".txt", ".md", ".markdown", ".json", ".ics", ".html",
    ".htm", ".xhtml", ".xml", ".vtt", ".srt", ".yaml", ".yml", ".rtf",
})

#: kind → parse_grid 的 fmt（原样回给前端的 CourseImportPreview.format）
_GRID_FORMATS = {
    "xlsx": "excel", "xls": "excel", "csv": "csv", "ods": "ods",
    "docx": "word", "pdf": "pdf", "html": "html",
}

#: 读不成网格的 kind → 可操作提示（用户手上多半就是这么个文件）
_UNSUPPORTED_KIND_HINTS = {
    "doc": "这是老式 Word(.doc) 文件：请在 Word/WPS 里另存为 .docx 后重试",
    "ppt": "这是老式 PowerPoint(.ppt) 文件：课表请改用 .xlsx / .ods / PDF / 网页(.html) 上传",
    "pptx": "这是演示文稿(.pptx)：课表请改用 .xlsx / .ods / PDF / 网页(.html) 上传",
    "odt": "这是文本文档(.odt)：课表需要表格，请另存为 .ods / .xlsx 或导出 PDF 后上传",
    "zip": "这是压缩包或已损坏的 Excel：请先解压，把里面的课表单独上传；"
           "也可在 Excel/WPS 里另存为 .xlsx 后重试",
}

#: 所有网格来源的支持格式说明（错误提示里列全，用户才知道还能怎么办）
SUPPORTED_SOURCES = (
    ".ics / .json 课表文本、Excel(.xlsx/.xls)、CSV·TSV、ODS、PDF、网页(.html)、"
    "Word(.docx)，或课表截图（PNG·JPG·WEBP·GIF·BMP·TIFF·AVIF）"
)


def _pymupdf():
    """惰性导入 PyMuPDF（本模块唯一的 PyMuPDF 入口）。

    为什么要在 catch_warnings 里 import：pytest.ini 把 DeprecationWarning 当错误，
    而 PyMuPDF 的 SWIG 扩展在 import 时会发这种警告；异常从 C 扩展初始化里抛出
    不是可捕获的 ImportError，而是直接把进程打成段错误。生产环境没有告警过滤器，
    这里屏蔽一次没有任何副作用。
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        import pymupdf
    return pymupdf


def detect_kind(data: bytes) -> str:
    """按魔数 + 容器结构判断来源类型（不信任文件扩展名）。

    保留既有语义：xlsx / xls / csv；新增 ods / docx / pdf / html。
    识别不出的文本仍是 "csv"（纯文本走 CSV 网格）；认出但是别的格式时返回
    它自己的扩展名（如 "doc"），由 `read_grid` 给出「另存为」这类可操作提示。
    """
    return _SNIFF_KINDS.get(formats.sniff(data), "csv")


def is_spreadsheet(data: bytes) -> bool:
    """是否是可当课表的表格文件（Excel/ODS 魔数，或能当 CSV 解码的纯文本）。"""
    ext = formats.sniff(data)
    if ext in (".xlsx", ".xls", ".ods"):
        return True
    if ext and ext not in _TEXTUAL_EXTS:  # 认出是 PDF/Word/图片… 时不能当文本读
        return False
    if b"\x00" in data[:2048]:  # 含 NUL 的二进制不是 CSV
        return False
    try:
        data[:4096].decode("utf-8")
        return True
    except UnicodeDecodeError:
        try:
            data[:4096].decode("gb18030")
            return True
        except UnicodeDecodeError:
            return False


def read_grid(data: bytes, kind: str | None = None) -> list[list[str]]:
    """课表来源字节 → 字符串网格（空单元格为 ""）。"""
    kind = kind or detect_kind(data)
    if kind == "xlsx":
        return _grid_from_xlsx(data)
    if kind == "xls":
        return _grid_from_xls(data)
    if kind == "csv":
        return _grid_from_csv(data)
    if kind == "ods":
        return _grid_from_ods(data)
    if kind == "docx":
        return _grid_from_docx(data)
    if kind == "pdf":
        return _grid_from_pdf(data)
    if kind == "html":
        return _grid_from_html(data)
    hint = _UNSUPPORTED_KIND_HINTS.get(kind)
    if hint:
        raise ImportParseError(hint)
    raise ImportParseError(f"无法识别的课表文件：支持 {SUPPORTED_SOURCES}，也可以直接粘贴课表文本")


def parse_spreadsheet(data: bytes, kind: str | None = None) -> dict:
    """课表来源字节 → {format, term_start_date, semester, entries}（入口）。"""
    kind = kind or detect_kind(data)
    return parse_grid(read_grid(data, kind), fmt=_GRID_FORMATS.get(kind, "excel"))


def _grid_from_xlsx(data: bytes) -> list[list[str]]:
    """openpyxl 读 .xlsx（多工作表时取数据最多的那个）。"""
    try:
        from openpyxl import load_workbook
    except ImportError as e:  # pragma: no cover - 依赖缺失分支
        raise ImportParseError("解析 .xlsx 需要 openpyxl：pip install openpyxl") from e
    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except (zipfile.BadZipFile, KeyError, OSError, ValueError) as e:
        raise ImportParseError(
            "无法读取这个 Excel 文件（可能已损坏或被加密）；"
            "可在 Excel/WPS 里另存为 .xlsx 或导出 CSV 后重试"
        ) from e
    try:
        best: list[list[str]] = []
        for ws in wb.worksheets:
            grid = _trim_grid([_row_values(row) for row in ws.iter_rows(values_only=True)])
            if _grid_size(grid) > _grid_size(best):
                best = grid
    finally:
        wb.close()
    if not best:
        raise ImportParseError("Excel 里没有找到任何数据行")
    return best


def _grid_from_xls(data: bytes) -> list[list[str]]:
    """xlrd 读老式 .xls（BIFF/OLE2，教务系统常见导出格式）。"""
    try:
        import xlrd
    except ImportError as e:  # pragma: no cover - 依赖缺失分支
        raise ImportParseError(
            "解析老式 .xls 需要 xlrd：pip install xlrd；"
            "或把文件另存为 .xlsx / CSV 后重试"
        ) from e
    try:
        book = xlrd.open_workbook(file_contents=data)
    except Exception as e:
        raise ImportParseError(
            "无法读取这个 .xls 文件（可能已损坏或被加密）；"
            "可在 Excel/WPS 里另存为 .xlsx 或导出 CSV 后重试"
        ) from e
    best: list[list[str]] = []
    for sheet in book.sheets():
        rows = [
            [_cell_text(sheet.cell_value(r, c), sheet.cell_type(r, c)) for c in range(sheet.ncols)]
            for r in range(sheet.nrows)
        ]
        grid = _trim_grid(rows)
        if _grid_size(grid) > _grid_size(best):
            best = grid
    if not best:
        raise ImportParseError(".xls 里没有找到任何数据行")
    return best


def _grid_from_csv(data: bytes) -> list[list[str]]:
    """CSV/TSV 文本 → 网格（UTF-8 优先，回退 GB18030，自动判分隔符）。"""
    text = decode_upload_text(data).lstrip("\ufeff")
    sample = text[:4096]
    try:
        delimiter = csv.Sniffer().sniff(sample, delimiters=",\t;").delimiter
    except csv.Error:
        delimiter = "\t" if sample.count("\t") > sample.count(",") else ","
    rows = [[_norm_cell(c) for c in row] for row in csv.reader(io.StringIO(text), delimiter=delimiter)]
    grid = _trim_grid(rows)
    if not grid:
        raise ImportParseError("CSV 里没有找到任何数据行")
    return grid


# ---------------------------------------------------------------------------
# PDF（教务系统/学院导出的课表 PDF：带表格线的直接读表，纯文字的按词框重建）
# ---------------------------------------------------------------------------

def _grid_from_pdf(data: bytes) -> list[list[str]]:
    """PDF 字节 → 网格：逐页先试表格识别，退化为词框坐标重建。

    PDF 课表有两种来源：教务系统导出（有表格线，`find_tables()` 直接给单元格，
    合并单元格也读得准）与排版稿（只有文字，没有线，只能拿词框坐标聚类还原行列）。
    两条路都受同一套页数/词框上限约束——课表总在文件开头，没必要为 200 页的
    培养方案 PDF 把请求拖死。
    """
    doc = _open_pdf(data)
    best: list[list[str]] = []
    try:
        for index in range(min(doc.page_count, MAX_PDF_PAGES)):
            try:
                grid = _pdf_page_grid(doc[index])
            except Exception:  # 单页损坏不该让整份课表 PDF 失败
                continue
            if _grid_rank(grid) > _grid_rank(best):
                best = grid
    finally:
        doc.close()
    if not best:
        raise ImportParseError(
            "这个 PDF 里没有可识别的课表文字：如果是扫描件（图片版），"
            "请改用「课表截图」上传，或改用 .ics / Excel / CSV 导入"
        )
    return best


def _open_pdf(data: bytes):
    """字节 → 已打开的 PyMuPDF 文档；打不开（损坏/加密）时抛 PdfUnreadableError。"""
    try:
        pymupdf = _pymupdf()
    except ImportError as e:  # pragma: no cover - 依赖缺失分支
        raise ImportParseError("解析 PDF 需要 PyMuPDF：pip install pymupdf；"
                               "或把课表另存为 .ics / Excel / CSV 后重试") from e
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as e:
        raise PdfUnreadableError(
            "无法识别的课表文件：这个 PDF 读不出来（可能已损坏或被加密）；"
            "请在浏览器/阅读器里重新另存为 PDF，或改用 .ics / Excel / CSV 导入"
        ) from e
    if getattr(doc, "needs_pass", False):
        doc.close()
        raise PdfUnreadableError(
            "无法识别的课表文件：这个 PDF 有密码保护；"
            "请先解除密码（另存为无密码 PDF），或改用 .ics / Excel / CSV 导入"
        )
    return doc


def _pdf_page_grid(page) -> list[list[str]]:
    """单页 → 网格：表格识别优先，找不到表格时用词框坐标重建。"""
    return _pdf_table_grid(page) or _pdf_words_grid(page)


def _pdf_table_grid(page) -> list[list[str]]:
    """页内表格 → 网格：取「非空单元格最多」的那张表（课表通常是页面主体）。"""
    finder = None
    if hasattr(page, "find_tables"):
        try:
            finder = page.find_tables()
        except Exception:  # 某页版式异常不该让整份 PDF 失败
            finder = None
    best: list[list[str]] = []
    for table in list(getattr(finder, "tables", None) or [])[:MAX_PDF_TABLES]:
        try:
            raw = table.extract()
        except Exception:
            continue
        grid = _trim_grid([[_norm_cell(c) for c in (row or ())] for row in (raw or [])])
        if _grid_rank(grid) > _grid_rank(best):
            best = grid
    return best


def _pdf_words_grid(page) -> list[list[str]]:
    """词框 → 网格：复用 OCR 那一套坐标聚类（PDF 的词框自带精确坐标）。

    自己不再写一份行列聚类：`boxes_to_grid` 已经把「同一格里的错行合并、
    相邻格子分得开」调好了，PDF 与截图只是坐标来源不同。
    """
    try:
        words = page.get_text("words")
    except Exception:
        return []
    boxes: list[tuple[str, float, float, float, float]] = []
    for item in (words or [])[:MAX_PDF_WORDS]:
        try:
            x0, y0, x1, y1, text = float(item[0]), float(item[1]), float(item[2]), float(item[3]), item[4]
        except (TypeError, ValueError, IndexError, KeyError):
            continue
        text = str(text or "").strip()
        if text:
            boxes.append((text, (x0 + x1) / 2, (y0 + y1) / 2, x1 - x0, y1 - y0))
    if not boxes:
        return []
    try:
        from app.infra.ingest.ocrgrid import boxes_to_grid
    except ImportError:  # pragma: no cover - 依赖缺失分支
        return []
    rect = getattr(page, "rect", None)
    rows = boxes_to_grid(
        boxes,
        int(getattr(rect, "width", 0) or 0),
        int(getattr(rect, "height", 0) or 0),
        max_rows=MAX_GRID_ROWS,
        max_cols=MAX_GRID_COLS,
    )
    return _unmerge_label_column(_trim_grid(rows))


#: 行标签前缀（「第1-2节」「1-2节」「08:00-09:40」「08:00」）；纯「1-2」不算——
#: 「1-16周 高等数学」这种以周次开头的课程格不能被误当成标签
_LABEL_PREFIX_RE = re.compile(
    r"^\s*("
    r"(?:第)?\s*\d{1,2}\s*(?:[-—~–、,，]\s*\d{1,2})?\s*节"
    r"|\d{1,2}\s*[:：]\s*\d{2}(?:\s*[-—~–]\s*\d{1,2}\s*[:：]\s*\d{2})?"
    r")\s*"
)


def _split_label_prefix(cell: str) -> tuple[str, str] | None:
    """'第1-2节 08:00-09:40 高等数学 李四' → ('第1-2节 08:00-09:40', '高等数学 李四')。

    只有整串都是标签（后面没有课程）或压根不像标签时返回 None。
    """
    rest = (cell or "").strip()
    consumed = ""
    while True:
        m = _LABEL_PREFIX_RE.match(rest)
        if not m:
            break
        consumed = f"{consumed} {m.group(1).strip()}".strip()
        rest = rest[m.end():].strip()
    if not consumed or (not rest and not consumed):
        return None
    return consumed, rest


def _unmerge_label_column(grid: list[list[str]]) -> list[list[str]]:
    """补救「左标签列和周一列被并成一列」的网格（纯文字 PDF/截图常见）。

    没有表格线时，左标签格「第1-2节 08:00-09:40」的文本框很宽，坐标聚类会把它和
    紧挨着的「周一」列并成一列；这一列随后会被 `_label_columns` 当成标签列整列丢掉，
    连里面的课程名一起消失。这里确认「第一列是周一 + 每格都能切出节次/时刻前缀」
    后把它拆回两列，拆不动就原样返回（宁可少认，不能把课程格拆坏）。
    """
    try:
        header_row, weekday_cols = _header_weekdays(grid)
    except ImportParseError:
        return grid
    if weekday_cols.get(0) != 1:
        return grid  # 第一列不是「周一」：没有被并进来
    data_rows = [row for i, row in enumerate(grid) if i != header_row and row and row[0]]
    if not data_rows:
        return grid
    splits = [_split_label_prefix(row[0]) for row in data_rows]
    if any(s is None for s in splits) or not any(rest for _, rest in splits):
        return grid
    out: list[list[str]] = []
    for i, row in enumerate(grid):
        if i == header_row:
            out.append([""] + list(row))
            continue
        split = _split_label_prefix(row[0]) if row else None
        if split:
            out.append([split[0], split[1]] + list(row[1:]))
        else:
            out.append(["", ""] + list(row[1:]))
    return _trim_grid(out)


def pdf_pages_to_images(
    data: bytes, *, max_pages: int = MAX_PDF_OCR_PAGES, dpi: int = MAX_PDF_OCR_DPI
) -> tuple[Path | None, list[Path]]:
    """扫描版 PDF → 每页一张临时 PNG（返回 (临时目录, 图片列表)）。

    调用方必须用 `cleanup_dir()` 清理临时目录（放在 finally 里）。
    PyMuPDF 不可用或渲染失败时返回 (None, [])，由调用方给出降级提示。
    """
    try:
        doc = _open_pdf(data)
    except ImportParseError:
        return None, []
    tmp_dir = Path(tempfile.mkdtemp(prefix="studyos-course-pdf-"))
    images: list[Path] = []
    try:
        for index, page in enumerate(doc):
            if index >= max_pages:
                break
            target = tmp_dir / f"page-{index + 1}.png"
            page.get_pixmap(dpi=dpi, alpha=False).save(str(target))
            images.append(target)
    except Exception:
        cleanup_dir(tmp_dir)
        return None, []
    finally:
        doc.close()
    if not images:
        cleanup_dir(tmp_dir)
        return None, []
    return tmp_dir, images


def cleanup_dir(path: Path | None) -> None:
    """删除临时目录（失败静默：系统会兜底清理，不能因此把导入结果变成失败）。"""
    if path is not None:
        shutil.rmtree(path, ignore_errors=True)


# ---------------------------------------------------------------------------
# 网页 HTML（教务系统「导出网页」/ 另存为网页：大量合并单元格）
# ---------------------------------------------------------------------------

def _grid_from_html(data: bytes) -> list[list[str]]:
    """网页课表 → 网格：取最大的一张 `<table>`，展开 rowspan/colspan。"""
    text = decode_upload_text(data)[:MAX_HTML_CHARS]
    tables = _html_tables(text)[:MAX_HTML_TABLES]
    best = _best_grid(_trim_grid(_expand_merged_rows(rows)) for rows in tables)
    if not best:
        raise ImportParseError(
            "这个网页里没有找到表格：请用浏览器打开课表页后「另存为网页(.html)」，"
            f"或改用 {SUPPORTED_SOURCES}"
        )
    return best


def _html_tables(text: str) -> list[list[list[tuple[str, int, int]]]]:
    """HTML → 每张表的原始行：``[[(文本, rowspan, colspan), ...], ...]``。

    lxml 容错最好（真实教务系统页面常有残缺标签），缺失时回退标准库 html.parser。
    """
    try:
        from lxml import html as lxml_html
    except ImportError:
        return _html_tables_stdlib(text)
    try:
        return _html_tables_lxml(text, lxml_html)
    except Exception:  # lxml 解析失败（空文档/编码怪异）时不留白
        return _html_tables_stdlib(text)


def _html_tables_lxml(text: str, lxml_html) -> list[list[list[tuple[str, int, int]]]]:
    from lxml import etree

    root = lxml_html.fromstring(text)
    etree.strip_elements(root, "script", "style", with_tail=False)
    tables: list[list[list[tuple[str, int, int]]]] = []
    for table in root.iter("table"):
        if len(tables) >= MAX_HTML_TABLES:
            break
        rows: list[list[tuple[str, int, int]]] = []
        for tr in table.iter("tr"):
            if _enclosing_table(tr) is not table:  # 嵌套子表自己也会被遍历到，别混进来
                continue
            cells = [
                (_html_element_text(cell),
                 _span_attr(cell.get("rowspan"), MAX_SPAN),
                 _span_attr(cell.get("colspan"), MAX_GRID_COLS))
                for cell in tr
                if isinstance(cell.tag, str) and cell.tag.lower() in ("td", "th")
            ]
            if cells:
                rows.append(cells)
            if len(rows) >= MAX_GRID_ROWS:
                break
        if rows:
            tables.append(rows)
    return tables


def _enclosing_table(element):
    """元素所属的最外层 `<table>`（用于把嵌套子表的行排除在外层之外）。"""
    parent = element.getparent()
    while parent is not None and parent.tag != "table":
        parent = parent.getparent()
    return parent


def _html_element_text(element) -> str:
    """单元格 → 文本：`<br>` 与块级标签当换行（课表里一行就是一个字段）。"""
    for br in element.iter("br"):
        br.tail = "\n" + (br.tail or "")
    for block in element.iter("p", "div", "li", "h1", "h2", "h3", "h4", "pre", "blockquote"):
        block.tail = "\n" + (block.tail or "")
    return _norm_lines(element.text_content())


class _TableCollector(HTMLParser):
    """标准库兜底：收集所有 `<table>` 的行列与合并跨度（lxml 缺失/失败时使用）。"""

    #: 视作换行的块级标签（课表里一个字段一行）
    _BLOCK_TAGS = frozenset({"br", "p", "div", "li", "h1", "h2", "h3", "h4", "pre"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[list]]] = []     # 表 → 行 → 单元格 [文本片段, rowspan, colspan]
        self._frames: list[list[list[list]]] = []    # 每层 <table> 的行列表（支持嵌套表）
        self._cells: list[list] = []                 # 已打开、还在攒文本的单元格

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "table":
            rows: list[list[list]] = []
            self.tables.append(rows)
            self._frames.append(rows)
            return
        if not self._frames:
            return
        if tag == "tr":
            if len(self._frames[-1]) < MAX_GRID_ROWS:
                self._frames[-1].append([])
        elif tag in ("td", "th"):
            row = self._frames[-1][-1] if self._frames[-1] else None
            if row is not None and len(row) < MAX_GRID_COLS:
                span = dict(attrs)
                cell = [[], _span_attr(span.get("rowspan"), MAX_SPAN),
                        _span_attr(span.get("colspan"), MAX_GRID_COLS)]
                row.append(cell)
                self._cells.append(cell)
        elif tag in self._BLOCK_TAGS and self._cells:
            self._cells[-1][0].append("\n")

    def handle_startendtag(self, tag: str, attrs) -> None:
        self.handle_starttag(tag, attrs)  # <br/> 换行；<td/> 这类空单元格同样吃掉

    def handle_endtag(self, tag: str) -> None:
        if tag == "table":
            if self._frames:
                self._frames.pop()
        elif tag in ("td", "th") and self._cells:
            self._cells.pop()

    def handle_data(self, data: str) -> None:
        if self._cells:
            self._cells[-1][0].append(data)


def _html_tables_stdlib(text: str) -> list[list[list[tuple[str, int, int]]]]:
    """标准库兜底解析：把收集到的可变单元格固化成 (文本, rowspan, colspan)。"""
    collector = _TableCollector()
    try:
        collector.feed(text)
        collector.close()
    except Exception:  # html.parser 对畸形文本基本不抛，兜底一次
        pass
    tables: list[list[list[tuple[str, int, int]]]] = []
    for rows in collector.tables[:MAX_HTML_TABLES]:
        table = [
            [(_norm_lines("".join(cell[0])), int(cell[1]), int(cell[2])) for cell in row]
            for row in rows if row
        ]
        if table:
            tables.append(table)
    return tables


def _expand_merged_rows(rows: list[list[tuple[str, int, int]]]) -> list[list[str]]:
    """展开 rowspan/colspan：合并单元格覆盖到的每一格都写上同一份文本。

    教务系统课表用合并单元格表示「同一门课连上两节 / 横跨两天」，展开后同一门课
    会在多个 (星期, 节次) 上各得一个时段——这正是我们要的结果，不能只留左上角。
    """
    grid: list[list[str]] = []
    pending: dict[int, list] = {}  # 列号 → [剩余行数, 文本]（上方 rowspan 落下来的格子）
    for raw in rows[:MAX_GRID_ROWS]:
        row: list[str] = []
        queue = list(raw)
        col = 0
        while col < MAX_GRID_COLS and (queue or any(c >= col for c in pending)):
            carry = pending.get(col)
            if carry is not None:
                row.append(carry[1])
                carry[0] -= 1
                if carry[0] <= 0:
                    del pending[col]
                col += 1
                continue
            text, rowspan, colspan = queue.pop(0)
            span = max(1, min(colspan, MAX_GRID_COLS - col))
            for k in range(span):
                row.append(text)
                if rowspan > 1:
                    pending[col + k] = [min(rowspan, MAX_SPAN) - 1, text]
            col += span
        grid.append(row)
    return grid


def _best_grid(grids) -> list[list[str]]:
    """多张候选网格 → 最像课表的那张（非空单元格最多，其次格子多）。"""
    best: list[list[str]] = []
    for grid in grids:
        if grid and _grid_rank(grid) > _grid_rank(best):
            best = grid
    return best


def _grid_rank(grid: list[list[str]]) -> tuple[int, int]:
    """网格排序键：先比非空单元格数（课表内容最全），再比格子总数。"""
    return sum(1 for row in grid for cell in row if cell), _grid_size(grid)


def _span_attr(value, limit: int) -> int:
    """rowspan/colspan → 夹紧到 [1, limit] 的跨度（恶意网页会写 999999）。"""
    try:
        span = int(str(value).strip())
    except (TypeError, ValueError):
        return 1
    return max(1, min(span, limit))


def _norm_lines(text: str) -> str:
    """网页/XML 文本 → 规范单元格文本：空白折叠、去掉空行、保留换行（限长）。"""
    lines = [re.sub(r"[ \t\u3000\xa0]+", " ", line).strip() for line in (text or "").splitlines()]
    return "\n".join(line for line in lines if line)[:MAX_CELL_CHARS]


# ---------------------------------------------------------------------------
# Word(.docx)：表格优先；纯文字课表（老师发的通知）按段落折成单列
# ---------------------------------------------------------------------------

def _grid_from_docx(data: bytes) -> list[list[str]]:
    """Word(.docx) → 网格：取最大的表格；整篇没有表格时按段落折成单列。"""
    try:
        import docx
    except ImportError as e:  # pragma: no cover - 依赖缺失分支
        raise ImportParseError(
            "解析 .docx 需要 python-docx：pip install python-docx；"
            "或另存为 PDF / CSV 后重试"
        ) from e
    try:
        document = docx.Document(io.BytesIO(data))
    except Exception as e:
        raise ImportParseError(
            "无法读取这个 Word 文档（可能已损坏、被加密，或其实是老式 .doc 改了扩展名）；"
            "请用 Word/WPS 另存为 .docx 后重试"
        ) from e
    try:
        best = _best_grid(
            _trim_grid([[_norm_cell(cell.text) for cell in row.cells[:MAX_GRID_COLS]]
                        for row in table.rows[:MAX_GRID_ROWS]])
            for table in list(getattr(document, "tables", None) or [])[:MAX_DOCX_TABLES]
        )
    except Exception as e:  # 病态表格（合并跨损坏节点等）：转成可操作提示，不抛底层异常
        raise ImportParseError(
            "这个 Word 文档的表格读不出来（文件可能已损坏）；"
            "请用 Word/WPS 打开后另存为 .docx，或另存为 PDF / CSV 再上传"
        ) from e
    if best:
        return best
    # 没有表格：老师常把课表写成一行一行的纯文字，按段折成单列网格交下游处理
    try:
        grid = _trim_grid([
            [_norm_cell(paragraph.text)]
            for paragraph in list(getattr(document, "paragraphs", None) or [])[:MAX_DOCX_PARAGRAPHS]
        ])
    except Exception as e:
        raise ImportParseError(
            "这个 Word 文档的正文读不出来（文件可能已损坏）；"
            "请用 Word/WPS 打开后另存为 .docx，或另存为 PDF / CSV 再上传"
        ) from e
    if not grid:
        raise ImportParseError("这个 Word 文档里没有表格也没有文字，请确认文件内容")
    return grid


# ---------------------------------------------------------------------------
# ODS（OpenDocument 电子表格：content.xml 就是一张张 table:table）
# ---------------------------------------------------------------------------

_TABLE_NS = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"


def _grid_from_ods(data: bytes) -> list[list[str]]:
    """OpenDocument 表格(.ods) → 网格：直接读容器里的 content.xml。"""
    xml_bytes = _read_ods_content(data)
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        raise ImportParseError(
            "这个 .ods 文件的内容已损坏；请在 LibreOffice/WPS 里另存为 .xlsx 或导出 CSV 后重试"
        ) from e
    tables: list[list[list[str]]] = []
    for element in root.iter():
        if _local_name(element.tag) != "table":
            continue
        tables.append(_trim_grid(_ods_table_grid(element)))
        if len(tables) >= MAX_ODS_TABLES:  # 表数量也要封顶：异常文件可能有上万张空表
            break
    best = _best_grid(tables)
    if not best:
        raise ImportParseError(".ods 里没有找到任何数据行，请确认文件里确实有课表内容")
    return best


def _read_ods_content(data: bytes) -> bytes:
    """ODS 容器 → content.xml 字节（缺失/过大/不是 ZIP 都转成可操作提示）。"""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf, zf.open("content.xml") as fp:
            # 有界读取：ZIP 头里声明的解压后大小可以撒谎，按实际读到的字节数封顶
            xml_bytes = fp.read(MAX_ODS_XML_BYTES + 1)
    except Exception as e:  # 解压失败的底层异常（zlib/zipfile）一律转成用户看得懂的提示
        raise ImportParseError(
            "无法读取这个 .ods 文件（不是有效的 OpenDocument 表格，或已损坏）；"
            "请在 LibreOffice/WPS 里另存为 .xlsx 或导出 CSV 后重试"
        ) from e
    if len(xml_bytes) > MAX_ODS_XML_BYTES:
        raise ImportParseError("这个 .ods 文件过大（content.xml 超过 8MB），请另存为 .xlsx 后重试")
    return xml_bytes


def _ods_table_grid(table) -> list[list[str]]:
    """一张 table:table → 网格（展开列/行重复与 covered-table-cell 占位）。

    重复次数必须夹紧：LibreOffice 用 `number-columns-repeated="1000"` 表示
    「后面全是空格」，而恶意文件会写 1000000——照单展开就是内存炸弹。
    """
    grid: list[list[str]] = []
    for row_el in _ods_row_elements(table):
        if len(grid) >= MAX_GRID_ROWS:
            break
        cells: list[str] = []
        row_repeat = 1
        for cell_el in row_el:
            if _local_name(cell_el.tag) not in ("table-cell", "covered-table-cell"):
                continue
            if len(cells) >= MAX_GRID_COLS:
                break
            text = _norm_lines(_ods_cell_text(cell_el))
            repeat = _repeat_attr(cell_el, "number-columns-repeated", MAX_GRID_COLS - len(cells))
            cells.extend([text] * repeat)
            row_repeat = max(row_repeat, _repeat_attr(cell_el, "number-rows-repeated", MAX_GRID_ROWS))
        row_repeat = min(row_repeat, MAX_GRID_ROWS - len(grid))
        grid.extend([list(cells)] * max(1, row_repeat))
    return grid


def _ods_row_elements(table) -> list:
    """表格 → 行元素（兼容 table-header-rows 这类分组容器，跳过嵌套子表）。"""
    rows: list = []

    def walk(node, depth: int) -> None:
        if depth > 4 or len(rows) >= MAX_GRID_ROWS:
            return
        for child in node:
            name = _local_name(child.tag)
            if name == "table-row":
                rows.append(child)
            elif name == "table":  # 嵌套子表会作为独立的 table 被遍历到
                continue
            elif name in ("table-header-rows", "table-rows", "table-row-group"):
                walk(child, depth + 1)

    walk(table, 0)
    return rows


def _ods_cell_text(cell) -> str:
    """单元格 → 文本：多个 text:p 按行连（text:s 是空格、text:tab 是制表符）。"""
    parts: list[str] = []
    for child in cell:
        name = _local_name(child.tag)
        if name == "p":
            parts.append(_ods_text(child))
        elif name == "s":
            parts.append(" " * _space_count(child))
        elif name == "tab":
            parts.append("\t")
        if len(parts) >= MAX_ODS_PARAGRAPHS:
            break
    return "\n".join(part for part in parts if part.strip())


def _ods_text(node, depth: int = 0) -> str:
    """text:p / text:span 树 → 文本（递归有深度上限，深层嵌套不会爆栈）。"""
    if depth > 8:
        return ""
    out: list[str] = [node.text or ""]
    for child in node:
        name = _local_name(child.tag)
        if name == "s":
            out.append(" " * _space_count(child))
        elif name == "tab":
            out.append("\t")
        elif name == "line-break":
            out.append("\n")
        elif name in ("span", "a", "p", "h", "ruby", "annotation"):
            out.append(_ods_text(child, depth + 1))
        # table/frame/image/note 这类内嵌对象不取文本（嵌套表格会单独解析）
        out.append(child.tail or "")
    return "".join(out)


def _space_count(node) -> int:
    """text:s 的空格数（`text:c`，缺省 1；夹紧到 100 防异常文件）。"""
    try:
        count = int(str(node.get("{%s}c" % _TABLE_NS, node.get("c", "1"))).strip())
    except (TypeError, ValueError):
        return 1
    return max(1, min(count, 100))


def _repeat_attr(element, name: str, limit: int) -> int:
    """`table:number-*-repeated` → 夹紧到 [1, limit] 的重复次数。"""
    value = element.get("{%s}%s" % (_TABLE_NS, name))
    if value is None:  # 少数导出工具漏写命名空间前缀，按本地名兜底
        value = next((v for k, v in element.attrib.items() if _local_name(k) == name), None)
    try:
        repeat = int(str(value).strip())
    except (TypeError, ValueError):
        return 1
    return max(1, min(repeat, limit))


def _local_name(tag) -> str:
    """ElementTree 标签 → 本地名（不关心命名空间前缀/URI 的写法差异）。"""
    return str(tag).rsplit("}", 1)[-1]


def combine_grids(grids) -> list[list[str]]:
    """多张网格 → 一张（逐页 OCR 或跨页课表拼起来后交给 parse_grid）。"""
    merged: list[list[str]] = []
    for grid in grids:
        merged.extend(grid)
    return _trim_grid(merged)


def _grid_size(grid: list[list[str]]) -> int:
    return len(grid) * (len(grid[0]) if grid else 0)


def decode_upload_text(data: bytes) -> str:
    """字节 → 文本：UTF-8(带 BOM) 优先，回退 GB18030（部分教务系统导出编码）。"""
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _row_values(row) -> list[str]:
    return [_norm_cell(v) for v in (row or ())]


def _cell_text(value, cell_type: int | None = None) -> str:
    """xlrd 单元格 → 文本（日期单元格按 YYYY-MM-DD 输出）。"""
    if cell_type == 3 and isinstance(value, float):  # 3 = XL_CELL_DATE
        formatted = _xldate_text(value)
        if formatted:
            return formatted
    return _norm_cell(value)


def _xldate_text(value: float) -> str:
    """xlrd 日期序列号 → 'YYYY-MM-DD'（无法解释时返回空串）。"""
    try:
        from xlrd.xldate import xldate_as_datetime

        dt = xldate_as_datetime(value, 0)
    except Exception:
        return ""
    if dt.year < 1900:
        return ""
    if (dt.hour, dt.minute) == (0, 0):
        return dt.strftime("%Y-%m-%d")
    return dt.strftime("%Y-%m-%d %H:%M")


def _norm_cell(v) -> str:
    """单元格值 → 去多余空白的字符串（datetime 走 ISO 写法，数字去掉多余的 .0）。"""
    if v is None or isinstance(v, bool):
        return ""
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d") if (v.hour, v.minute) == (0, 0) else v.strftime("%Y-%m-%d %H:%M")
    if isinstance(v, date):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, dtime):
        return f"{v.hour:02d}:{v.minute:02d}"
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else str(v)
    return re.sub(r"[ \t]+", " ", str(v).replace("\u3000", " ").strip())[:MAX_CELL_CHARS]


def _trim_grid(rows: list[list[str]]) -> list[list[str]]:
    """裁掉全空行与全空的尾列、补齐成矩形（限行限列）。

    全空尾列必须裁：ODS/Excel 导出习惯把每行补到固定列宽
    （`number-columns-repeated="50"` 之类），留着会让网格凭空多出几十个空列。
    """
    cleaned = [list(row[:MAX_GRID_COLS]) for row in rows[:MAX_GRID_ROWS]]
    cleaned = [row for row in cleaned if any(row)]
    width = max((len(r) for r in cleaned), default=0)
    grid = [r + [""] * (width - len(r)) for r in cleaned]
    while grid and width and not any(row[width - 1] for row in grid):
        for row in grid:
            row.pop()
        width -= 1
    return grid


# ---------------------------------------------------------------------------
# 网格 → 课程条目
# ---------------------------------------------------------------------------

def grid_semester_meta(grid: list[list[str]]) -> tuple[str, str | None]:
    """扫表头 → (学期名, 开学日期 ISO)。找不到返回 ("", None)。"""
    semester = ""
    start: str | None = None
    for row in grid[:MAX_HEADER_SCAN]:
        for cell in row:
            if not cell:
                continue
            if not semester:
                m = _SEMESTER_RE.search(cell)
                if m:
                    # 三种写法各占一个分组：学年第一学期 / 学年学期：1 / 2026-2027-1
                    raw = m.group(3) or m.group(4) or m.group(5) or ""
                    num = {"一": "1", "二": "2", "三": "3", "四": "4"}.get(raw, raw)
                    semester = f"{m.group(1)}-{m.group(2)}-{num}"
                else:
                    m = _SEMESTER_LOOSE_RE.search(cell)
                    if m:
                        semester = f"{m.group(1)}-{m.group(2)}"
            if not start:
                start = _start_date_in(grid, cell)
    if start and not semester:
        semester = guess_semester(date.fromisoformat(start))
    return semester, start


def _start_date_in(grid: list[list[str]], cell: str) -> str | None:
    """表头单元格 → 开学日期（优先「第1周(9月1日)」，其次完整日期）。"""
    m = _WEEK1_RE.search(cell)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        candidate = _safe_date(_academic_year(grid, month), month, day)
        if candidate:
            return candidate.isoformat()
    m = _DATE_RE.search(cell)
    if m:
        candidate = _safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if candidate:
            return candidate.isoformat()
    return None


def _academic_year(grid: list[list[str]], month: int) -> int:
    """从表头学年（2025-2026学年）推年份：8 月后开学取前一年，否则后一年。"""
    for row in grid[:MAX_HEADER_SCAN]:
        for cell in row:
            m = re.search(r"(20\d{2})\s*[-—~–]\s*(20\d{2})\s*学年", cell or "")
            if m:
                y1, y2 = int(m.group(1)), int(m.group(2))
                return y1 if month >= 8 else y2
    return date.today().year


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _header_weekdays(grid: list[list[str]]) -> tuple[int, dict[int, int]]:
    """找星期表头行 → (行号, {列号: 星期})。"""
    best_row, best_map = -1, {}
    for i, row in enumerate(grid[:MAX_HEADER_SCAN]):
        found: dict[int, int] = {}
        for j, cell in enumerate(row):
            wd = _weekday_of(cell)
            if wd and wd not in found.values():
                found[j] = wd
        if len(found) > len(best_map):
            best_row, best_map = i, found
    if len(best_map) < 2:
        raise ImportParseError(
            "没找到「周一…周日」表头：请确认上传的是二维课表（一行一个节次、"
            "一列一个星期），或改用 .ics / JSON 课表文本"
        )
    # 表头必须从周一开始连续排：OCR 重建的网格可能把「周一」表头并进左标签列，
    # 于是表头从周二开始（整体错位一天），这里按「最左数据列 = 周一」自纠。
    # 只在确认「周一」缺席时纠偏——否则会把左侧的时刻列误当成周一。
    first_col = min(best_map)
    if 1 not in best_map.values() and best_map[first_col] > 1:
        return best_row, {c: c - first_col + 1 for c in best_map}
    return best_row, best_map


def _weekday_of(cell: str) -> int | None:
    """单元格 → 星期（周一 / 星期一 / 礼拜一 / mon / 周一(1) 等写法）。

    必须带「周/星期/礼拜」前缀或英文缩写才算星期——OCR 会把「菊次间」这类
    标签误成「次」，裸字不认，否则表头行会被认错、后面整体错位。
    """
    s = (cell or "").strip().lower()
    if not s:
        return None
    head = re.split(r"[(（\s]", s, maxsplit=1)[0].strip()
    if head in _EN_WEEKDAY:
        return _EN_WEEKDAY[head]
    m = re.match(r"^(?:星期|周|礼拜)\s*([一二三四五六日天1-7])$", head)
    return _WEEKDAY_CN[m.group(1)] if m else None


def _times_in(s: str) -> tuple[str, str] | None:
    """文本里的第一个时刻（或时间段）→ (start, end)；没有则 None。"""
    m = _TIME_RE.search(s or "")
    if not m:
        return None
    start = _norm_clock(m.group(1))
    if not start:
        return None
    end = _norm_clock(m.group(2))
    if not end or _minutes(end) <= _minutes(start):
        end = _plus(start, 45)
    return start, end


def _plus(t: str, delta: int) -> str:
    total = min(_minutes(t) + delta, 24 * 60 - 1)
    return f"{total // 60:02d}:{total % 60:02d}"


def _norm_clock(s: str | None) -> str:
    """'8：00' / '8:00:00' → 'HH:MM'（容错全角冒号）。"""
    if not s:
        return ""
    try:
        return _parse_time(s.replace("：", ":").replace(" ", ""))
    except ImportParseError:
        return ""


def _row_label_period(cell: str) -> tuple[int, int] | None:
    """行标签 → (首节, 末节)；不是「第N节 / N-M节 / 第N大节」这类节次写法则 None。"""
    s = (cell or "").strip()
    if not s or _TIME_RE.search(s):
        return None
    major = _MAJOR_PERIOD_RE.match(s)
    if major:
        return _MAJOR_PERIODS.get(_major_number(major.group(1)))
    if re.search(r"[^\d\s第节\-—~–、,，至到和课]", s):  # 混了别的字（课程名/备注）
        return None
    m = _PERIOD_RE.match(s)
    if not m:
        return None
    first = int(m.group(1))
    last = int(m.group(2)) if m.group(2) else first
    if last < first:
        first, last = last, first
    if not (1 <= first <= 12 and 1 <= last <= 12):
        return None
    return first, last


def _major_number(token: str) -> int:
    """「第一大节」的序号词 → 1~10；认不出返回 0。"""
    s = (token or "").strip()
    if s.isdigit():
        return int(s)
    if s == "十":
        return 10
    if len(s) == 2 and s[0] == "十":          # 十一…十九
        return 10 + _MAJOR_CN_NUM.get(s[1], 0)
    if len(s) == 2 and s[1] == "十":          # 二十
        return _MAJOR_CN_NUM.get(s[0], 0) * 10
    return _MAJOR_CN_NUM.get(s, 0) if len(s) == 1 else 0


def _row_label_time(cell: str) -> tuple[str, str] | None:
    """行标签 → (start, end)：节次写法查标准时间表，时刻写法直接采用。"""
    s = (cell or "").strip()
    if not s:
        return None
    times = _times_in(s)
    if times:
        return times
    period = _row_label_period(s)
    if not period:
        return None
    first, last = period
    return period_to_time(first), period_end_time(last)


def _footer_row(grid: list[list[str]], start: int) -> int:
    """数据区结束行（备注/说明行之前）。"""
    for i in range(start, len(grid)):
        if any(cell.strip().startswith(FOOTER_MARKERS) for cell in grid[i] if cell):
            return i
    return len(grid)


def _row_times(grid: list[list[str]], header_row: int, end_row: int) -> dict[int, tuple[str, str]]:
    """数据行 → (start, end)：优先左侧标签列，其次行内任意列（最右常写节次时刻）。"""
    times: dict[int, tuple[str, str]] = {}
    periods: dict[int, tuple[int, int]] = {}
    for i in range(header_row + 1, end_row):
        row = grid[i]
        candidates = [row[0]] + list(row[1:4]) if row else []
        found = next((t for t in (_row_label_time(c) for c in candidates) if t), None)
        if not found:
            found = next((t for t in (_row_label_time(c) for c in (row[1:] if row else [])) if t), None)
        if found:
            times[i] = found
            continue
        if row and row[0]:
            period = _row_label_period(row[0])
            if period:
                periods[i] = period
    if not times:
        for i, (first, last) in periods.items():
            times[i] = (period_to_time(first), period_end_time(last))
    return times


def _label_columns(grid: list[list[str]], header_row: int, end_row: int,
                   weekday_cols: dict[int, int]) -> set[int]:
    """识别「行标签列」（节次/时间/备注列），这些列不承载课程。

    二维课表的左标签列里常写着「第3-4节 10:00-11:40」这种「节次 + 时刻」组合，
    有些版式还会把这类文字重复到最右一列。若把这种列当成星期列，就会解析出
    「第3-4节」这种假课程。判定分两种：
    - 没有星期表头的列：出现节次或两个以上节次/时刻标记即视为标签列；
    - 有星期表头的列：只有**同时**出现星期词与多个节次/时刻标记才算——OCR
      重建的网格可能把「周一」表头和左标签列并进同一列（此时该列是标签列，
      不能因为带表头就当星期列）。
    """
    label_cols: set[int] = set()
    width = len(grid[0]) if grid else 0
    if not _has_time_column(grid, header_row, end_row):
        # 没有独立时刻列时，行标签列就是唯一的时间来源，剔掉它整张表就废了；
        # 此时只剔「表头不是星期且列内堆着节次/时刻」的纯标签列（见下）。
        for col in range(width):
            if col in weekday_cols and _row_label_period(grid[header_row][col] if col < len(grid[header_row]) else ""):
                continue
            text = " ".join(
                grid[r][col] for r in range(header_row + 1, min(end_row, header_row + 6))
                if col < len(grid[r]) and grid[r][col]
            )
            if _count_time_marks(text) >= 2:
                label_cols.add(col)
        return {c for c in label_cols if c not in weekday_cols}
    for col in range(width):
        hits = 0
        filled = 0
        has_weekday = False
        for row in range(header_row + 1, min(end_row, header_row + 1 + 12)):
            if col >= len(grid[row]):
                continue
            text = grid[row][col]
            if not text:
                continue
            filled += 1
            if _weekday_of(text):
                has_weekday = True
            if _count_time_marks(text) >= 2 or _row_label_period(text):
                hits += 1
        if has_weekday:
            if _weekday_of(grid[header_row][col] if col < len(grid[header_row]) else ""):
                continue  # 真正的星期列：表头是星期且列内没有堆节次标签
            if not hits:
                continue
        if hits and hits >= max(1, (filled + 1) // 2):
            label_cols.add(col)
    if len(label_cols) >= len(weekday_cols):  # 兜底：不能把星期列全剔光
        return set()
    return label_cols


def _has_time_column(grid: list[list[str]], header_row: int, end_row: int) -> bool:
    """这张表是否有独立的时刻列（列内多数格子是「08:00-09:40」这类时刻）。

    决定「左标签列」能不能整列丢掉：有时刻列时它只是节次列（可丢），
    没有时刻列时它同时承担时刻来源（丢了就没时间可算）。
    「第N大节」同样算时刻来源——大节编号能查到标准节次时间。
    """
    width = len(grid[0]) if grid else 0
    for col in range(width):
        filled = hits = 0
        for row in range(header_row + 1, min(end_row, header_row + 8)):
            if col >= len(grid[row]) or not grid[row][col]:
                continue
            filled += 1
            if _times_in(grid[row][col]) or _row_label_period(grid[row][col]):
                hits += 1
        if hits and hits >= max(1, (filled + 1) // 2):
            return True
    return False


def _count_time_marks(text: str) -> int:
    """文本里出现的节次/时刻标记个数（用于判断这一格是不是「节次+时间」标签）。"""
    marks = len(re.findall(r"第?\s*\d{1,2}\s*[-—~–、,，]?\s*\d{0,2}\s*节", text or ""))
    marks += len(_TIME_RE.findall(text or ""))
    marks += len(_MAJOR_PERIOD_RE.findall(text or ""))
    return marks


def parse_grid(grid: list[list[str]], fmt: str = "excel") -> dict:
    """二维课表网格 → {format, term_start_date, semester, entries}。

    行 = 节次/时刻（时间取自行标签），列 = 星期（取自星期表头行），
    单元格 = 一门或多门课程（多行文本里的周次/老师/地点就地抽取）。
    """
    if not grid or not any(any(c for c in row) for row in grid):
        raise ImportParseError("这个课表文件是空的，没有可解析的内容")
    header_row, weekday_cols = _header_weekdays(grid)
    end_row = _footer_row(grid, header_row + 1)
    label_cols = _label_columns(grid, header_row, end_row, weekday_cols)
    data_cols = {c: wd for c, wd in weekday_cols.items() if c not in label_cols}
    if not data_cols:
        raise ImportParseError(
            "课表里没有可用的星期列：请确认「周一…周日」表头下有课程内容，"
            "或改用 .ics / JSON 课表文本导入"
        )
    row_times = _row_times(grid, header_row, end_row)
    if not row_times:
        raise ImportParseError(
            "没找到节次/时间列：课表左侧需要有「第1-2节」或「08:00-09:40」这样的"
            "行标签（也可改用 .ics / JSON 课表文本导入）"
        )

    # 先统计全表词频（按单个词）：课程名跨格/跨天重复，老师名只在少数格出现；
    # 像老师/像教室的词单独记下来，免得它们靠出现次数挤掉真正的课名
    token_freq: dict[str, int] = {}
    teacher_like: set[str] = set()
    for i in sorted(row_times):
        for col in data_cols:
            if col >= len(grid[i]):
                continue
            for chunk in _split_cell(grid[i][col]):
                _, tok_teacher, tok_location, _ = _split_course_text(chunk)
                for token in re.split(r"[\s,，;；|/]+", chunk):
                    if not token:
                        continue
                    if _looks_like_teacher(token) or _looks_like_location(token):
                        teacher_like.add(token)
                    else:
                        token_freq[token] = token_freq.get(token, 0) + 1
                if tok_teacher:
                    teacher_like.add(tok_teacher)
                if tok_location:
                    teacher_like.add(tok_location)

    merged: dict[str, dict] = {}
    for i in sorted(row_times):
        start, end = row_times[i]
        for col, weekday in sorted(data_cols.items()):
            if col >= len(grid[i]):
                continue
            for chunk in _split_cell(grid[i][col]):
                name, teacher, location, weeks_s = _split_course_text(
                    chunk, token_freq, teacher_like
                )
                key = _clean_name(name)
                if not key:
                    continue
                entry = merged.setdefault(
                    key, {"name": key, "code": "", "teacher": "", "location": "", "slots": []}
                )
                entry["teacher"] = entry["teacher"] or teacher
                entry["location"] = entry["location"] or location
                weeks = _weeks_of(weeks_s)
                hit = next(
                    (s for s in entry["slots"]
                     if s["weekday"] == weekday and s["start"] == start and s["end"] == end),
                    None,
                )
                if hit:
                    hit["weeks"] = sorted(set(hit["weeks"]) | set(weeks))
                    hit["location"] = hit["location"] or location
                    hit["teacher"] = hit["teacher"] or teacher
                else:
                    entry["slots"].append({
                        "weekday": weekday, "start": start, "end": end, "weeks": weeks,
                        "location": location, "teacher": teacher,
                    })
                if len(entry["slots"]) >= MAX_SLOTS:
                    break
    if not merged:
        raise ImportParseError(
            "课表里没有解析出任何课程：请确认单元格里有课程名（如「高等数学」），"
            "或改用 .ics / 粘贴 JSON 课表文本"
        )

    semester, start_date = grid_semester_meta(grid)
    entries = list(merged.values())
    for e in entries:
        e["slots"] = sorted(e["slots"], key=lambda s: (s["weekday"], s["start"]))[:MAX_SLOTS]
    return {
        "format": fmt,
        "term_start_date": start_date,
        "semester": semester,
        "entries": entries[:MAX_ENTRIES],
    }


def _weeks_of(weeks_s: str) -> list[int]:
    """单元格里抽出的周次文本 → 周次列表（含单双周）；抽不出返回 []。"""
    if not weeks_s:
        return []
    # 去掉 OCR/排版带来的括号包裹（'(1-16周)' / '[周]' 写法 → '1-16周'），parse_weeks 不认括号
    cleaned = weeks_s.strip().strip("()（）[]【】").strip()
    try:
        return parse_weeks(cleaned)
    except ImportParseError:
        return []


def _split_cell(cell: str) -> list[str]:
    """单元格 → 候选课程块。

    多行文本按「课程名行」重新起块（老师/地点/周次行跟随上一门课）；**空行是
    更硬的分块边界**——教务系统的格子里常把同一时段的两门课用空行隔开
    （一门课的老师/地点行本身也像课程名行，光看内容分不开）；单行文本原样返回。
    """
    text = (cell or "").strip()
    if not text:
        return []
    # 逐个换行切（不用 [\n\r]+）：空行要作为「硬分块边界」被留下来
    lines = [ln.strip() for ln in re.split(r"[\n\r]", text)]
    stripped = [ln for ln in lines if ln]
    if len(stripped) <= 1:
        return stripped or [text]
    # 每一行前面有没有空行 → 是否强制另起一块（第一行不算，前面没有块）
    breaks: list[bool] = []
    pending = False
    for ln in lines:
        if not ln:
            pending = True
            continue
        breaks.append(pending and bool(breaks))
        pending = False
    chunks: list[str] = []
    current: list[str] = []
    # 姓名行（「郝松」「李四」）与课名行字面同形，只有整块里出现别行的课名时才好判：
    # 块里还有别的行能当课名时，姓名行一律当附属行，不另起一块
    named = any(_looks_like_course_line(ln) and not _is_bare_teacher_line(ln) for ln in stripped)
    for ln, hard_break in list(zip(stripped, breaks))[:MAX_CELL_CHUNKS]:
        starts = _looks_like_course_line(ln) and not (named and _is_bare_teacher_line(ln))
        if current and (hard_break or starts):
            chunks.append(" ".join(current))
            current = [ln]
        else:
            current.append(ln)
    if current:
        chunks.append(" ".join(current))
    return chunks[:MAX_CELL_CHUNKS]


def _first_detail_index(lines: list[str]) -> int:
    """多行文本里第一行「附属信息」（不是课程名）的下标；全是附属信息则 0。

    只看第一个词：单行也能用，且不回头调用 _split_course_text，免得递归。
    """
    for index, line in enumerate(lines):
        words = [w for w in re.split(r"[\s,，;；|/]+", line.strip()) if w]
        if words and _looks_like_course_word(words[0]):
            return index
    return 0


def _looks_like_course_word(word: str) -> bool:
    """这个词像课程名的开头（而不是老师名/教室号/周次这类附属信息）。"""
    s = (word or "").strip()
    if not s or _looks_like_detail(s) or _looks_like_teacher(s) or _looks_like_location(s):
        return False
    if _TEACHER_TITLE_RE.search(s):
        return False
    return bool(re.search(r"[\u4e00-\u9fffA-Za-z]", s))


def _looks_like_course_line(line: str) -> bool:
    """这一行像课程名（而不是老师/地点/周次这类附属信息）。

    课表单元格里「高等数学 / 张三 / 教3-101 / 1-16周」各占一行，后三行都属于
    上一门课，不能各自变成一门课——所以老师名、教室号这类行一律不算课程名行。
    教务系统的老师行有两种写法：「张新义(讲师)」带职称括注，或干脆只写姓名
    （「郝松」）。两种都必须按附属行处理，否则一门课会被拆成两门。
    """
    s = (line or "").strip()
    if not s or _looks_like_detail(s) or _looks_like_teacher(s) or _looks_like_location(s):
        return False
    if _TEACHER_TITLE_RE.search(s):
        return False
    if not re.search(r"[\u4e00-\u9fffA-Za-z]", s):
        return False
    if len(s) > 30:
        return False
    return _looks_like_course_word(re.split(r"[\s,，;；|/]+", s)[0])


def _is_bare_teacher_line(line: str) -> bool:
    """整行就是一个姓名（无职称、无空格），如「郝松」。

    课程名与姓名在字面上分不开（「电路」课与「电路」老师都合法），但课表里
    姓名行总跟在课名行后面：只要单元格里还有别的行可能是课名，就把它当附属行，
    别让它另起一门课。
    """
    s = (line or "").strip()
    s = re.sub(r"[（(]\s*[)）]", "", s).strip()   # 「郝松()」这类空括注先清掉
    return bool(s) and not _TEACHER_TITLE_RE.search(s) and _looks_like_teacher(s)


def _looks_like_detail(line: str) -> bool:
    """像「1-16周」「周一」「08:00-09:40」这样的附属行。"""
    s = (line or "").strip()
    if _is_week_only(s):
        return True
    if re.fullmatch(r"(?:周|星期|礼拜)?[一二三四五六日天1-7]", s):
        return True
    if re.fullmatch(r"[\d\-—~–:：\s.]+", s):
        return True
    return False


def _is_week_only(s: str) -> bool:
    """整行只有周次标记（「1-16周」「4-6,8-18[周]」「1-16周(单)」）→ True。

    交给 _take_week_token 判定而不是用一条正则硬吃：周字两侧的括号形态太多，
    而 3.14 的 re 在含括号的写法上不可靠（见 _WEEK_RUN_RE 处的说明）。
    """
    if not s or "周" not in s:
        return False
    rest, weeks = _take_week_token(s)
    return bool(weeks) and not rest


def _week_body_before(prefix: str) -> str:
    """「周」字左边的周次本体（4-6,8-18 / 1,3,5-8 / 8）；不像周次则返回空串。

    整段都是手写扫描，一条正则都不用：CPython 3.14.4 的 re 在「量词 + 可选组」
    上不会回溯（`\\d{1,2}` 匹配掉「16周」里的 16 之后匹配不上 '周' 就直接放弃，
    连 `\\d{1,2}周` 都匹配不了「1-16周」），课表里这类写法必须绕开 re。

    周次本体可能整个被括号包着（``4-6,8-18[周]``），此时周字左边紧挨着的是
    ``[`` 而不是数字，所以得逐字当起点试一遍，取第一个站得住的起点。
    """
    tail = prefix.rstrip(" \t\u3000")
    best: tuple[int, str] | None = None
    for start in range(len(tail)):            # 每个字都当一次起点，取最长的合法本体
        found = _week_body_from(tail[start:], start)
        if not found:
            continue
        head, body = found
        if head < 0 or head + len(body) > len(tail):
            continue                              # 起点被切到串外：这一刀不成立
        if head > 0 and tail[head - 1] in "-—~–":  # 左界：别把「3-101」腰斩
            continue
        if all(ch in "（([【" for ch in tail[head + len(body):]):
            if best is None or len(body) > len(best[1]):
                best = (head, body)               # 右界：本体要贴到「周」字（可隔一层括号）
    return best[1] if best else ""

def _week_body_from(tail: str, base: int = 0) -> tuple[int, str] | None:
    """把「周」左侧的文本按逗号分段校验 → (本体起点, 周次本体)；不像周次则 None。

    每段只能是「数字」或「数字-数字」，取值不超过 MAX_WEEK_LABEL（否则
    「教3-101周」这种房间号加周字会被当成周次）；段首段尾的残渣允许丢掉
    （``4-6,8-18[`` 里紧挨周字的 ``[`` 括号本来就不算周次本体）。
    """
    kept: list[str] = []
    found: int | None = None
    offset = 0
    for part in tail.split(","):
        start = 0
        while start < len(part) and not part[start].isdigit():
            start += 1                        # 段首残渣（「[4-6」里的「[」）
        if start >= len(part):
            break
        seg = part[start:]
        # 连续吃数字/连接号，吃完的位置就是本体结束位置（段尾残渣丢掉）
        end = 0
        while end < len(seg) and (seg[end].isdigit() or seg[end] in "-—~–"):
            end += 1
        seg = seg[:end]
        nums = _WEEK_DIGITS_RE.findall(seg)
        if not nums or not all(1 <= int(n) <= MAX_WEEK_LABEL for n in nums):
            break
        if any(len(n) > 1 and n.startswith("0") for n in nums):   # 「101」切出的「01」不算周次
            break
        if len(nums) not in (1, 2):           # 一段里只能是「7」或「1-16」两种形态
            break
        if found is None:
            found = base + offset + start
        kept.append(seg)
        offset += len(part) + 1
        if len(kept) >= 8:                    # 周次段数护栏（异常文本不至于越啃越长）
            break
    return (found, ",".join(kept)) if kept and found is not None else None


def _take_week_token(raw: str) -> tuple[str, str]:
    """从单元格文本里摘出第一个周次标记 → (去掉标记的剩余文本, 周次文本)。

    认「1-16周」「4-6,8-18[周]」「第1周」「1-16周(单)」等写法。周次本体可能整个
    被括号包着（周字写在括号里，如 ``4-6,8-18[周]``），右侧还常跟「(单)/(双)」；
    返回的周次文本统一成「本体 + 周 + 单/双」，交给 parse_weeks 归一化。
    """
    text = raw or ""
    # 从右往左试每个「周」字，取第一个两侧都能站住的标记：文本里「周」可能先以
    # 单双周括注的形式出现（「1-16周(单)」只是拿左边那段是拼不出完整周次的）
    for pos in [i for i, ch in enumerate(text) if ch == "周"][::-1]:
        if pos == 0:
            continue
        body = _week_body_before(text[:pos])
        if not body:
            continue
        head = text[:pos].rstrip(" \t\u3000")
        start = head.rindex(body)             # 本体在原文里的起点（head 可能还带着左括号）
        # 周次本体可能整个被括号包着（「4-6,8-18[周]」「(1-16周)」）：连着括号一起摘。
        # 只摘与本体紧邻的那一层，课名自带的「【选修】」前面隔着空格，不会被误伤
        while start > 0 and text[start - 1] in "（([【":
            start -= 1
        end = pos + 1
        weeks = body
        nxt = text[end:end + 1]
        if nxt in "（([【" and text[end + 1:end + 2] in ("单", "双"):
            weeks = f"{body}周{text[end + 1]}"         # 1-16周(单) / 1-16周[双]
            end += 2
        else:
            weeks = f"{body}周"
        # 被摘掉的标记两边若还挂着配套的右括号，一并清掉
        while end < len(text) and text[end] in "）)]】":
            end += 1
        return f"{text[:start]} {text[end:]}".strip(), weeks
    return raw, ""


def _split_course_text(
    raw: str,
    freqs: dict[str, int] | None = None,
    teacher_like: set[str] | None = None,
) -> tuple[str, str, str, str]:
    """单元格文本 → (课程名, 老师, 地点, 周次文本)。

    支持写法：``高等数学 张三 教3-101 1-16周``、``高等数学(1-16周)@教3-101``、
    ``【选修】大学英语 1-16周(单)``、``高等数学\\n张三\\n教3-101``。
    """
    s = (raw or "").strip()
    if not s:
        return "", "", "", ""
    s, weeks_s = _take_week_token(s)
    lines = [ln.strip() for ln in re.split(r"[\n\r]+", s) if ln.strip()]
    if lines:
        # 一格里可能塞了两门课（空行分隔），每门课的老师/地点行只属于它上面那门。
        # 从第一行课程名开始取，前面的附属行（周次/老师/地点）属于上一门课，丢掉
        s = lines[_first_detail_index(lines)]
    else:
        s = s.strip()
    teacher = ""
    location = ""
    rest_lines = lines[1:]
    for line in rest_lines:
        if not teacher:
            found = _TEACHER_RE.search(line)
            if found:
                teacher = found.group(1).strip()
                continue
        if not location:
            found = _LOCATION_RE.search(line)
            if found:
                location = (found.group(1) or found.group(2) or "").strip()
                continue
        if not location and _looks_like_location(line):
            location = line
            continue
        if not teacher and _TEACHER_TITLE_RE.search(line):
            # 「张新义(讲师)」这类老师行：剥掉职称括注剩下的就是姓名
            stripped = _TEACHER_TITLE_RE.sub("", line).strip()
            if _looks_like_teacher(stripped):
                teacher = stripped
                continue
        if not teacher and _looks_like_teacher(line):
            teacher = line
            continue
        if not location and not _looks_like_teacher(line):
            location = line          # 剩下的附属行当地点（实验楼/球场这类写法）
            continue
        s = f"{s} {line}".strip()    # 认不出来的并回正文，交给下面的启发式
    m = _TEACHER_RE.search(s)
    if m:
        teacher = teacher or m.group(1)
        s = f"{s[:m.start()]} {s[m.end():]}".strip()
    m = _LOCATION_RE.search(s)
    if m:
        location = location or (m.group(1) or m.group(2) or "").strip()
        s = f"{s[:m.start()]} {s[m.end():]}".strip()
    if not location:
        m = _ROOM_SUFFIX_RE.search(s)
        if m and _looks_like_location(m.group(1)):
            location = m.group(1)
            s = s[:m.start()].strip()
    if not teacher:
        # 无标签的教师名：中文名后面常跟着职称括注（张新义(讲师)）、多名用逗号相连
        # （吴海平(副教授),钱彦(副教授)）。先剥职称，剩下的才可能是姓名；只在还没
        # 认出教师时做，免得抢走课名里的括注（如「体育（Ⅲ）」）。
        s, stripped = _split_teacher_names(s)
        if stripped:
            teacher = stripped
    s = _FOOTNOTE_RE.sub("", s).strip()
    s = re.sub(r"[（(]\s*[)）]", "", s).strip()  # 「郝松()」这类空括注
    parts = [p for p in re.split(r"[\s,，;；|/]+", s) if p]
    name = parts[0] if parts else ""
    bracket = _NAME_BRACKET_RE.match(name)
    if bracket and bracket.group(2):
        name = bracket.group(2).strip()
    # 先按打分选课名：不能默认「第一个词」是课名（OCR 常把老师/地点写在前），
    # 也不能默认「像老师名的词」是老师——「高等数学」四个字同样像中文姓名。
    candidates = list(parts)
    # 若其余候选全是短词（≤3 字，只可能是老师名/周次残片），那么「像教室号」的
    # 那个词其实就是课程名（格子编号被当课名的课确实存在），别把它当地点扣分
    name = max(
        candidates,
        key=lambda cand: _name_score(
            cand,
            following=len(cand) < max(len(p) for p in candidates),
            freqs=freqs,
            teacher_like=teacher_like,
        ),
    )
    if not teacher:
        rest = [p for p in candidates if p != name]
        for cand in rest:
            if _looks_like_teacher(cand):
                teacher = cand
                break
    if not location:
        for cand in candidates:
            if cand != name and _looks_like_location(cand):
                location = cand
                break
    if not location:
        # 课名已经选走，剩下的若像教室（教3-101、综合楼A201）就属地地点
        rest = [p for p in candidates if p != name and p != teacher]
        location = next((p for p in rest if _looks_like_location(p)), "")
    return name.strip(), teacher.strip(), location.strip(), weeks_s


def _name_score(
    token: str,
    following: bool = False,
    freqs: dict[str, int] | None = None,
    teacher_like: set[str] | None = None,
) -> int:
    """课程名打分（越大越像课名），用于 OCR 打乱字段顺序后的兜底选择。

    结构是「基础分 - 罚分」再乘一个好词系数，避免罚分被长度优势压过去：
    1. **长度**：中文课程名普遍比老师名长（高等数学/计算机组成原理 vs 李四/赵六）；
    2. **位置**：课名一般写在老师/地点**之前**，排在更长的词后面就减分——这样
       「李四 高等数学 教1-202」也能选对；
    3. **罚分**：像教室号（教4-108）重罚——课名总含中文，纯编号形态的不是课名；
       像老师名（≤3 字中文）轻罚；别处已经扮演过老师/地点的词更重罚；
    4. **全表重复次数**：课程名跨格跨天重复，老师名只出现在自己的格子附近。
    """
    key = (token or "").strip()
    if not key:
        return -100000
    base = len(key) - (6 if following else 0)
    penalty = 0
    if _looks_like_location(key):
        penalty -= 14
    if _looks_like_teacher(key):
        penalty -= 4
    if key in (teacher_like or set()):
        penalty -= 8
    return base + penalty + (freqs or {}).get(key, 0)


# 课程名常见词素：中文姓名不会用这些字，用它把「大学英语」这类 4 字课名与
# 「欧阳修文」这类 4 字姓名区分开
_COURSE_WORD_MARKERS = (
    "学", "论", "语", "数学", "物理", "化学", "原理", "设计", "导论", "概论",
    "分析", "基础", "技术", "实验", "教程", "史", "思想", "经济", "法律", "导",
)


def _looks_like_teacher(s: str) -> bool:
    """中文姓名（2~4 字，可带「等」或多人）；含课程名常见词素的不算。"""
    key = (s or "").strip().rstrip("等")
    if not re.fullmatch(r"[\u4e00-\u9fff]{2,4}(?:[,，、][\u4e00-\u9fff]{2,4})*", key):
        return False
    if len(key) <= 3:
        return True  # 2~3 字纯中文基本就是人名（李四/赵六/周八）
    return not any(marker in key for marker in _COURSE_WORD_MARKERS)


def _split_teacher_names(s: str) -> tuple[str, str]:
    """剥掉教师名后的职称括注 → (剩余文本, 教师名)。

    教务系统的老师行是「张新义(讲师)」「吴海平(副教授),钱彦(副教授)」：职称括注既
    不是课名也不是地点，留着会污染课名打分，所以先按「拿掉括注后仍是姓名」把它摘出来。
    只在还没认出教师时调用，且要求剥壳后确实是姓名形态，「体育（Ⅲ）」这类课名不受影响。
    """
    if "(" not in s and "（" not in s and "[" not in s and "［" not in s:
        return s, ""
    matches = list(_TEACHER_TITLE_RE.finditer(s))
    if not matches:
        return s, ""
    # 最后一个职称括注之前的那一段才是姓名（课名部分可能也带括号，如「体育（Ⅲ）」）；
    # 姓名的左界取最后一个分隔符：课名与老师用空格分开时，别把整串课名当姓名。
    # 多名教师「吴海平(副教授),钱彦(副教授)」逐个从右往左剥，前面的老师也一并收进来。
    names: list[str] = []
    spans: list[tuple[int, int]] = []
    for index in range(len(matches) - 1, -1, -1):
        start = 0 if index == 0 else matches[index - 1].end()
        head = s[start:matches[index].start()]
        cut = max(head.rfind(ch) for ch in " \t,，、;；")
        if cut >= 0:
            start += cut + 1
            head = head[cut + 1:]
        head = head.strip(" \t,，、;；")
        if not _looks_like_teacher(head):
            break
        names.insert(0, head)
        spans.insert(0, (start, matches[index].end()))
    if not names:
        return s, ""
    keep = s
    for start, end in reversed(spans):           # 从右往左删，位置才不会错位
        keep = keep[:start] + keep[end:]
    return re.sub(r"\s+", " ", keep).strip(" ,，、;；"), "、".join(names)


def _looks_like_location(s: str) -> bool:
    """教室/楼栋写法：含楼馆室区场、教3-101、I-101、Ⅳ-A103、A201、205 等。

    「物理实验中心」「工程训练中心」这类没有门牌号的地点也常见，按「中心/实验室」
    这类场所后缀一起认。
    """
    s = (s or "").strip()
    if len(s) < 2 or len(s) > 30:
        return False
    if re.search(r"[楼馆室区场]|中心$|实验室$", s):
        return True
    if re.search(r"教\s*\d", s):
        return True
    # 「Ⅳ-A103」「I-101」：罗马数字楼号（全角 Ⅰ-Ⅻ 与 ASCII i/v/x 混用），
    # 中间用 -/— 连房间号；纯数字格要 2~4 位，免得把学年 2026 当成教室
    if re.fullmatch(r"[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫivxIVX]{1,4}\s*[-—–]\s*[A-Za-z]?\d{2,4}", s):
        return True
    if re.fullmatch(r"[A-Za-z]?\d{2,4}[A-Za-z]?", s) and not _looks_like_year(s):
        return True
    return bool(re.search(r"[A-Za-z]\s*\d{2,4}", s))


def _looks_like_year(s: str) -> bool:
    """「2026」这种 4 位年份不算教室（表头里到处都是）。"""
    return bool(re.fullmatch(r"20\d{2}", (s or "").strip()))
