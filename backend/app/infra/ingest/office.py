"""二进制办公文档抽取：PDF / Word / PowerPoint / Excel / OpenDocument / 旧版 OLE2。

这些格式的共同点是「文本藏在二进制或 ZIP 容器里」，抽取策略分三层：

1. **有官方库就用官方库**（python-docx / python-pptx / openpyxl / xlrd / PyMuPDF），
   保留段落、表格、演讲者备注等结构信息 —— DDL 与考点大量藏在表格和备注里；
2. **PDF 走子进程**（`pdfworker.py`）：PyMuPDF 是原生扩展，损坏文件可能让进程崩溃，
   放在子进程里解码，最坏情况只丢这一次上传，不会带走整个 API；
3. **旧版二进制（.doc/.ppt）**没有可靠的纯 Python 解析器，做**有界文本抓取**：
   扫描容器里的可读文本串，宁可少抽也不要乱码，并在 `warnings` 里说明局限。

所有循环都有页/表/行/列/字符上限，异常一律转成空结果 + 中文提示，绝不外抛。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from app.infra.ingest.base import ExtractedFile, clean_text, join_blocks, text_slice

MAX_TEXT_CHARS = 200_000
MAX_SHEET_ROWS = 300
MAX_SHEET_COLS = 40
MAX_SHEETS = 12
MAX_TABLE_ROWS = 200
MAX_TABLE_COLS = 20
MAX_TABLES = 50
MAX_SLIDES = 300
MAX_ODF_ROWS = 500
MAX_ODF_COLS = 60
MAX_OLE_BYTES = 24 * 1024 * 1024

PDF_TIMEOUT = 30          # 单次 PDF 解码超时（秒）：坏文件不能占住请求
PDF_OCR_MAX_PAGES = 10    # 扫描版 PDF 的 OCR 页数上限
PDF_OCR_DPI = 180


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

def extract_pdf(path: Path) -> ExtractedFile:
    """PDF：文本层优先；文本层为空时标记 `needs_ocr`（由调用方决定是否 OCR）。"""
    try:
        child = subprocess.run(
            [sys.executable, str(Path(__file__).with_name("pdfworker.py")), str(path)],
            capture_output=True, text=True, timeout=PDF_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ExtractedFile("", warnings=["PDF 解析超时或失败，请确认文件未损坏，或另存为文本后重试"])
    if child.returncode:
        return ExtractedFile("", warnings=["PDF 无法解析，可能已加密或损坏；可另存为 PDF/A 或文本后重试"])
    try:
        value = json.loads(child.stdout)
    except ValueError:
        return ExtractedFile("")
    text = clean_text(str(value.get("text") or ""))
    pages = int(value.get("pages") or 0)
    needs_ocr = bool(value.get("needs_ocr"))
    result = ExtractedFile(text, needs_ocr=needs_ocr, pages=pages)
    if needs_ocr and not text:
        result.with_warning("这份 PDF 没有文本层（扫描件），将尝试 OCR 识别")
    elif needs_ocr and text:
        result.with_warning("PDF 里只有少量文本，可能有整页图片内容未被识别")
    return result


def _pymupdf():
    """惰性导入 PyMuPDF（本模块唯一的入口）。

    必须在 `warnings.catch_warnings()` 里 import：PyMuPDF 的 SWIG 扩展在导入期会发
    DeprecationWarning，而本项目的 pytest.ini 把该告警升级为错误 —— 异常从 C 扩展
    初始化里抛出**不是可捕获的 ImportError，而是直接把进程打成段错误（SIGSEGV）**。
    生产环境没有告警过滤器，屏蔽一次没有任何副作用。
    """
    import warnings

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            import pymupdf
    except ImportError:
        return None
    return pymupdf


def pdf_pages_to_images(path: Path, *, max_pages: int = PDF_OCR_MAX_PAGES,
                        dpi: int = PDF_OCR_DPI) -> tuple[Path | None, list[Path]]:
    """扫描版 PDF → 每页一张临时 PNG（返回 (临时目录, 图片列表)）。

    调用方必须用 `cleanup_dir()` 清理临时目录（放在 finally 里）。
    PyMuPDF 不可用或渲染失败时返回 (None, [])，由调用方降级提示。
    """
    pymupdf = _pymupdf()
    if pymupdf is None:
        return None, []
    tmp_dir = Path(tempfile.mkdtemp(prefix="studyos-pdf-ocr-"))
    images: list[Path] = []
    try:
        with pymupdf.open(str(path)) as doc:
            for index, page in enumerate(doc):
                if index >= max_pages:
                    break
                pix = page.get_pixmap(dpi=dpi)
                target = tmp_dir / f"page-{index + 1}.png"
                pix.save(str(target))
                images.append(target)
    except Exception:
        cleanup_dir(tmp_dir)
        return None, []
    if not images:
        cleanup_dir(tmp_dir)
        return None, []
    return tmp_dir, images


def cleanup_dir(path: Path | None) -> None:
    """删除临时目录（失败静默：临时目录由系统兜底清理，不能因此影响上传结果）。"""
    if path is None:
        return
    shutil.rmtree(path, ignore_errors=True)


# ---------------------------------------------------------------------------
# Word (.docx)
# ---------------------------------------------------------------------------

def extract_docx(path: Path) -> ExtractedFile:
    """Word 文档：正文段落 + 表格 + 页眉页脚（按文档顺序交错，表格不丢结构）。"""
    try:
        from docx import Document
        from docx.table import Table
        from docx.text.paragraph import Paragraph
    except ImportError:
        return ExtractedFile("", warnings=["缺少 python-docx，无法解析 Word 文档"])
    try:
        doc = Document(str(path))
    except Exception:
        return ExtractedFile("", warnings=["Word 文档无法解析，可能已损坏或被加密"])
    blocks: list[str] = []
    try:
        title = (doc.core_properties.title or "").strip()
        if title:
            blocks.append(f"[标题] {text_slice(title, 200)}")
        for child in doc.element.body.iterchildren():
            tag = child.tag.rsplit("}", 1)[-1]
            if tag == "p":
                text = Paragraph(child, doc).text.strip()
                if text:
                    blocks.append(text)
            elif tag == "tbl":
                table = _docx_table_text(Table(child, doc))
                if table:
                    blocks.append(table)
        for section in doc.sections[:5]:
            for part in (section.header, section.footer):
                try:
                    lines = [p.text.strip() for p in part.paragraphs if p.text.strip()]
                except Exception:
                    continue
                if lines:
                    blocks.append("[页眉/页脚] " + " / ".join(lines[:5]))
    except Exception:
        return ExtractedFile(clean_text("\n".join(blocks)), warnings=["Word 文档解析中断，仅提取了部分内容"])
    if not blocks:
        return ExtractedFile("", needs_ocr=False,
                             warnings=["Word 文档里没有抽取到文字（可能是纯图片内容）"])
    return ExtractedFile(join_blocks(blocks))


def _docx_table_text(table) -> str:
    rows: list[str] = []
    for row in table.rows[:MAX_TABLE_ROWS]:
        cells = [re.sub(r"\s+", " ", cell.text).strip() for cell in row.cells[:MAX_TABLE_COLS]]
        if any(cells):
            rows.append(" | ".join(cells))
    if not rows:
        return ""
    return "[表格]\n" + "\n".join(rows)


# ---------------------------------------------------------------------------
# PowerPoint (.pptx)
# ---------------------------------------------------------------------------

def extract_pptx(path: Path) -> ExtractedFile:
    """演示文稿：每页文字（含表格/图表标题）+ 演讲者备注。"""
    try:
        from pptx import Presentation
    except ImportError:
        return ExtractedFile("", warnings=["缺少 python-pptx，无法解析演示文稿"])
    try:
        prs = Presentation(str(path))
    except Exception:
        return ExtractedFile("", warnings=["演示文稿无法解析，可能已损坏或被加密"])
    parts: list[str] = []
    slide_count = 0
    try:
        for index, slide in enumerate(prs.slides, 1):
            if index > MAX_SLIDES:
                break
            slide_count = index
            slide_text: list[str] = []
            for shape in slide.shapes:
                slide_text.extend(_pptx_shape_text(shape))
            notes = _pptx_notes(slide)
            if notes:
                slide_text.append(f"[备注] {notes}")
            if slide_text:
                parts.append(f"[第{index}页] " + " ".join(slide_text))
        slides_total = len(prs.slides._sldIdLst)
    except Exception:
        return ExtractedFile(join_blocks(parts), pages=slide_count,
                             warnings=["演示文稿解析中断，仅提取了部分页面"])
    if not parts:
        return ExtractedFile("", pages=slides_total,
                             warnings=["演示文稿里没有抽取到文字（可能是纯图片幻灯片）"])
    return ExtractedFile(join_blocks(parts), pages=slides_total)


def _pptx_shape_text(shape) -> list[str]:
    """单个形状 → 文本片段：文本框 / 表格 / 图表标题都覆盖。"""
    out: list[str] = []
    try:
        if shape.has_text_frame:
            for para in shape.text_frame.paragraphs:
                value = "".join(run.text for run in para.runs).strip()
                if value:
                    out.append(value)
    except Exception:
        pass
    try:
        if getattr(shape, "has_table", False):
            for row in shape.table.rows[:MAX_TABLE_ROWS]:
                cells = [cell.text.strip().replace("\n", " ") for cell in row.cells[:MAX_TABLE_COLS]]
                if any(cells):
                    out.append(" | ".join(cells))
    except Exception:
        pass
    try:
        if getattr(shape, "has_chart", False):
            chart = shape.chart
            if chart.has_title and chart.chart_title.text_frame.text.strip():
                out.append(f"[图表] {chart.chart_title.text_frame.text.strip()}")
            for series in list(chart.series)[:10]:
                name = getattr(series, "name", "") or ""
                values = [str(v) for v in list(series.values)[:20] if v is not None]
                if name or values:
                    out.append(f"[图表数据] {name}: {', '.join(values)}")
    except Exception:
        pass
    return out


def _pptx_notes(slide) -> str:
    try:
        frame = slide.notes_slide.notes_text_frame
    except Exception:
        return ""
    if frame is None:
        return ""
    try:
        return " ".join(p.text.strip() for p in frame.paragraphs if p.text.strip())[:2000]
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Excel (.xlsx / .xls)
# ---------------------------------------------------------------------------

def extract_xlsx(path: Path) -> ExtractedFile:
    """Excel 工作簿：逐工作表渲染成表格文本（成绩单/名单里的日期与名称最容易丢）。"""
    try:
        from openpyxl import load_workbook
    except ImportError:
        return ExtractedFile("", warnings=["缺少 openpyxl，无法解析 Excel 工作簿"])
    try:
        wb = load_workbook(str(path), read_only=True, data_only=True)
    except Exception:
        return ExtractedFile("", warnings=["Excel 无法解析，可能已损坏、被加密或为 .xls 格式"])
    blocks: list[str] = []
    try:
        for name in wb.sheetnames[:MAX_SHEETS]:
            ws = wb[name]
            rendered = _sheet_text(name, ws)
            if rendered:
                blocks.append(rendered)
    except Exception:
        return ExtractedFile(join_blocks(blocks), warnings=["Excel 解析中断，仅提取了部分工作表"])
    finally:
        try:
            wb.close()
        except Exception:
            pass
    if not blocks:
        return ExtractedFile("", warnings=["Excel 里没有抽取到数据"])
    return ExtractedFile(join_blocks(blocks))


def _sheet_text(name: str, ws) -> str:
    rows: list[str] = []
    truncated = False
    for row in ws.iter_rows(values_only=True):
        if len(rows) >= MAX_SHEET_ROWS:
            truncated = True
            break
        cells = [_cell_str(v) for v in (row or ())[:MAX_SHEET_COLS]]
        while cells and not cells[-1]:
            cells.pop()
        if any(cells):
            rows.append(" | ".join(cells))
    if not rows:
        return ""
    head = f"[工作表] {text_slice(str(name), 80)}"
    if truncated:
        head += f"（仅前 {MAX_SHEET_ROWS} 行）"
    return head + "\n" + "\n".join(rows)


def extract_xls(path: Path) -> ExtractedFile:
    """老式 .xls（BIFF/OLE2）：xlrd 逐表渲染。"""
    try:
        import xlrd
    except ImportError:
        return ExtractedFile("", warnings=["缺少 xlrd，无法解析老式 .xls，请另存为 .xlsx"])
    try:
        book = xlrd.open_workbook(filename=str(path))
    except Exception:
        return ExtractedFile("", warnings=["老式 .xls 无法解析，请在 Excel/WPS 里另存为 .xlsx 后重试"])
    blocks: list[str] = []
    for sheet in book.sheets()[:MAX_SHEETS]:
        rows: list[str] = []
        truncated = False
        for r in range(min(sheet.nrows, MAX_SHEET_ROWS)):
            if sheet.nrows > MAX_SHEET_ROWS:
                truncated = True
            cells = [_cell_str(_xlrd_value(sheet, r, c)) for c in range(min(sheet.ncols, MAX_SHEET_COLS))]
            while cells and not cells[-1]:
                cells.pop()
            if any(cells):
                rows.append(" | ".join(cells))
        if rows:
            head = f"[工作表] {text_slice(str(sheet.name), 80)}"
            if truncated:
                head += f"（仅前 {MAX_SHEET_ROWS} 行）"
            blocks.append(head + "\n" + "\n".join(rows))
    if not blocks:
        return ExtractedFile("", warnings=["老式 .xls 里没有抽取到数据"])
    return ExtractedFile(join_blocks(blocks))


def _xlrd_value(sheet, row: int, col: int):
    """xlrd 单元格 → Python 值（日期单元格转 datetime，否则会得到 45000 这样的序列号）。"""
    value = sheet.cell_value(row, col)
    if sheet.cell_type(row, col) == 3:  # XL_CELL_DATE
        try:
            from xlrd.xldate import xldate_as_datetime

            return xldate_as_datetime(value, book_datemode(sheet))
        except Exception:
            return value
    return value


def book_datemode(sheet) -> int:
    try:
        return sheet.book.datemode
    except Exception:
        return 0


def _cell_str(value) -> str:
    """单元格值 → 展示字符串（日期走 ISO，浮点去掉多余 .0，超长截断）。"""
    if value is None or isinstance(value, bool):
        return ""
    if hasattr(value, "strftime"):
        try:
            if (value.hour, value.minute, value.second) == (0, 0, 0):
                return value.strftime("%Y-%m-%d")
            return value.strftime("%Y-%m-%d %H:%M")
        except Exception:
            return str(value)
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else str(value)
    return text_slice(re.sub(r"\s+", " ", str(value)), 160)


# ---------------------------------------------------------------------------
# OpenDocument (.odt / .ods / .odp)
# ---------------------------------------------------------------------------

_ODF_NS = {
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
    "draw": "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0",
}


def extract_odf(path: Path, kind: str = "odt") -> ExtractedFile:
    """OpenDocument 文本/表格/演示：直接读 `content.xml`（无需额外依赖）。"""
    try:
        with zipfile.ZipFile(path) as zf:
            if "content.xml" not in zf.namelist():
                return ExtractedFile("", warnings=["OpenDocument 文件缺少 content.xml，可能已损坏"])
            raw = zf.read("content.xml")
    except (zipfile.BadZipFile, OSError, KeyError):
        return ExtractedFile("", warnings=["OpenDocument 文件无法读取，可能已损坏或被加密"])
    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError:
        return ExtractedFile("", warnings=["OpenDocument 内容解析失败，文件可能已损坏"])
    if kind == "ods":
        blocks = _odf_sheet_blocks(root)
    elif kind == "odp":
        blocks = _odf_slide_blocks(root)
    else:
        blocks = _odf_document_blocks(root)
    if not blocks:
        return ExtractedFile("", warnings=["OpenDocument 里没有抽取到文字内容"])
    return ExtractedFile(join_blocks(blocks))


def _tag(node) -> str:
    return node.tag.rsplit("}", 1)[-1] if isinstance(node.tag, str) else ""


def _odf_text(node) -> str:
    """元素内的文本：`text:p` 段落换行、`text:s` 空格、`text:tab` 制表符。"""
    parts: list[str] = []
    if node.text:
        parts.append(node.text)
    for child in node:
        name = _tag(child)
        if name == "p":
            parts.append(_odf_text(child) + "\n")
            continue
        if name == "s":
            count = child.get(f"{{{_ODF_NS['text']}}}c") or "1"
            try:
                parts.append(" " * max(1, min(50, int(count))))
            except ValueError:
                parts.append(" ")
            continue
        if name == "tab":
            parts.append("\t")
            continue
        if name in ("line-break",):
            parts.append("\n")
            continue
        if name == "span":
            parts.append(_odf_text(child))
            continue
        parts.append(_odf_text(child))
        if child.tail:
            parts.append(child.tail)
    return "".join(parts)


def _odf_document_blocks(root) -> list[str]:
    """odt：正文段落 + 表格（按文档顺序，表格单元格里的段落不再重复输出）。"""
    blocks: list[str] = []
    for body in root.iter():
        if _tag(body) != "text":
            continue
        for node in body:
            _collect_odf_block(node, blocks)
    if blocks:
        return blocks
    # 结构不规范的 odt（没有 office:text 包裹）兜底：直接扫段落
    return [t for t in (_odf_text(n).strip() for n in root.iter() if _tag(n) in ("p", "h")) if t]


def _collect_odf_block(node, blocks: list[str], depth: int = 0) -> None:
    """按顺序收集块级内容：段落/标题成行，表格整体渲染，容器递归下钻。"""
    if depth > 6:
        return
    name = _tag(node)
    if name in ("p", "h"):
        text = _odf_text(node).strip()
        if text:
            blocks.append(text)
    elif name == "table":
        rendered = _odf_table(node)
        if rendered:
            blocks.append(rendered)
    elif name in ("section", "list", "list-item", "frame", "text-box", "header", "footer"):
        for child in node:
            _collect_odf_block(child, blocks, depth + 1)


def _odf_sheet_blocks(root) -> list[str]:
    """ods：逐工作表渲染（含 number-columns-repeated 展开，带上限）。"""
    blocks: list[str] = []
    for table in root.iter():
        if _tag(table) != "table":
            continue
        name = table.get(f"{{{_ODF_NS['table']}}}name") or "工作表"
        rendered = _odf_table(table, max_rows=MAX_ODF_ROWS, max_cols=MAX_ODF_COLS)
        if rendered:
            blocks.append(f"[工作表] {text_slice(name, 80)}\n{rendered}")
    return blocks


def _odf_table(table, max_rows: int = MAX_TABLE_ROWS, max_cols: int = MAX_TABLE_COLS) -> str:
    """ODF 表格 → `a | b | c` 文本（重复列/行必须夹紧，恶意文件会写 1000000）。"""
    rows: list[str] = []
    for row in table:
        if _tag(row) != "table-row":
            continue
        if len(rows) >= max_rows:
            rows.append("…")
            break
        try:
            repeat = int(row.get(f"{{{_ODF_NS['table']}}}number-rows-repeated") or "1")
        except ValueError:
            repeat = 1
        cells: list[str] = []
        for cell in row:
            if _tag(cell) != "table-cell":
                continue
            try:
                span = int(cell.get(f"{{{_ODF_NS['table']}}}number-columns-repeated") or "1")
            except ValueError:
                span = 1
            span = max(1, min(span, max_cols - len(cells)))
            value = re.sub(r"\s+", " ", _odf_text(cell)).strip()
            cells.extend([value] * span)
            if len(cells) >= max_cols:
                break
        while cells and not cells[-1]:
            cells.pop()
        line = " | ".join(cells)
        if line:
            rows.extend([line] * max(1, min(repeat, max_rows - len(rows))))
    return "\n".join(rows)


def _odf_slide_blocks(root) -> list[str]:
    """odp：逐页抽文字。"""
    blocks: list[str] = []
    for index, page in enumerate(root.iter(), 1):
        if _tag(page) != "page":
            continue
        lines = [t.strip() for t in (_odf_text(node).strip() for node in page.iter()
                                     if _tag(node) == "p") if t]
        if lines:
            blocks.append(f"[第{index}页] " + " ".join(lines[:60]))
    return blocks


# ---------------------------------------------------------------------------
# 旧版 OLE2（.doc / .ppt）：有界文本抓取
# ---------------------------------------------------------------------------

_OLE_TEXT_MIN = 4          # 连续可读字符的最短长度
_OLE_ASCII_MIN = 24        # 单字节 ASCII 串的最短长度（过滤二进制噪声）


def extract_ole_text(path: Path, label: str = "旧版 Office 文档") -> ExtractedFile:
    """旧版 .doc/.ppt：扫描容器里的可读文本串（UTF-16LE 与单字节两轮）。"""
    try:
        with path.open("rb") as f:
            data = f.read(MAX_OLE_BYTES)
    except OSError:
        return ExtractedFile("")
    utf16 = _utf16_runs(data)
    ascii_runs = _ascii_runs(data)
    blocks = []
    if utf16:
        blocks.append("\n".join(utf16))
    if ascii_runs:
        blocks.append("\n".join(ascii_runs))
    if not blocks:
        return ExtractedFile(
            "", warnings=[f"{label}没能抽取到文字（旧版二进制格式兼容性有限），建议用 Office/WPS 另存为 .docx/.pptx 后重试"]
        )
    return ExtractedFile(
        clean_text("\n".join(blocks)),
        warnings=[f"这是旧版二进制格式，只能尽力抽取文本（格式与图片会丢失）；建议另存为 .docx/.pptx 后重新上传"],
    )


def _decode_unit(hi: int, lo: int) -> str:
    """UTF-16LE 单元 → 字符（含代理对拼接与全角标点）。"""
    code = hi | (lo << 8)
    if 0xD800 <= code <= 0xDBFF:
        return ""          # 代理对高位单独出现时丢弃，避免抛异常
    if 0xDC00 <= code <= 0xDFFF:
        return ""
    if code in (0x09, 0x0A, 0x0D):
        return " "
    if 0x20 <= code <= 0x7E or 0x3000 <= code <= 0x303F or 0x4E00 <= code <= 0x9FFF \
            or 0xFF00 <= code <= 0xFFEF or 0x2010 <= code <= 0x203B or code in (0xA0,):
        return chr(code)
    return ""


def _utf16_runs(data: bytes) -> list[str]:
    """扫描 UTF-16LE 可读串（中文 Word/PPT 正文的实际存储方式）。

    命中不可读单元时**只前进 1 字节**（而不是 2）：段间填充会让对齐错位，
    单字节步进能在下一个位置重新对齐。
    """
    runs: list[str] = []
    current: list[str] = []
    limit = len(data) - 1
    index = 0
    while index < limit:
        ch = _decode_unit(data[index], data[index + 1])
        if ch:
            current.append(ch)
            index += 2
            continue
        if len(current) >= _OLE_TEXT_MIN:
            runs.append("".join(current).strip())
        current = []
        index += 1
    if len(current) >= _OLE_TEXT_MIN:
        runs.append("".join(current).strip())
    return [r for r in runs if len(r) >= _OLE_TEXT_MIN][:4000]


_ASCII_RUN = re.compile(rb"[\x20-\x7e\t]{%d,}" % _OLE_ASCII_MIN)


def _ascii_runs(data: bytes) -> list[str]:
    """扫描单字节可读串：过滤掉「看着像二进制」的串（必须含空格或字母数字混排）。"""
    out: list[str] = []
    for match in _ASCII_RUN.finditer(data[: 4 * 1024 * 1024]):
        raw = match.group().decode("ascii", errors="ignore").strip()
        if len(raw) < _OLE_ASCII_MIN:
            continue
        letters = sum(c.isalpha() or c.isspace() for c in raw)
        if letters / max(1, len(raw)) < 0.75:      # 全是符号的串是二进制噪声
            continue
        out.append(raw)
        if len(out) >= 500:
            break
    return out


__all__ = [
    "MAX_TEXT_CHARS", "PDF_OCR_DPI", "PDF_OCR_MAX_PAGES", "cleanup_dir",
    "extract_docx", "extract_odf", "extract_ole_text", "extract_pdf",
    "extract_pptx", "extract_xls", "extract_xlsx", "pdf_pages_to_images",
]
