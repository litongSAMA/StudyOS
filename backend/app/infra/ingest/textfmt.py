"""文本类格式抽取：纯文本 / 表格文本 / 网页 / RTF / Notebook / 字幕 / 数据 / 代码。

这些格式的共同点是「本身就是文本（或文本的容器）」，
所以抽取质量的关键不在解析器，而在三件事：

1. **编码**：教务系统与老课件大量使用 GBK/GB18030，个别是 Big5/Shift-JIS，
   用 UTF-8 硬解会得到满屏乱码，进而把 DDL 抽取彻底带偏 —— 统一走编码阶梯；
2. **结构**：HTML 的标题/段落/表格、Notebook 的单元格、字幕的时间轴、
   代码的顶层定义，都要转成「带语义分节」的纯文本，LLM 才能据此提炼考点；
3. **规模**：一切循环都有行列/条目/字符上限，异常或恶意文件不能拖垮服务。
"""
from __future__ import annotations

import codecs
import csv
import html as _html
import io
import json
import re
from html.parser import HTMLParser
from pathlib import Path

from app.infra.ingest.base import ExtractedFile, clean_text, join_blocks, text_slice

MAX_TEXT_CHARS = 200_000
MAX_TABLE_ROWS = 400          # 表格渲染行数上限
MAX_TABLE_COLS = 60
MAX_NOTEBOOK_CELLS = 400
MAX_SUBTITLE_LINES = 3000
MAX_CODE_SUMMARY = 20

#: 编码阶梯：中文教务/课件环境实际会遇到的编码，按命中概率排序
#: （utf-8-sig 只在有 BOM 时用，否则会把普通 UTF-8 文件也标成 utf-8-sig）
ENCODINGS: tuple[str, ...] = (
    "utf-8", "gb18030", "big5", "shift_jis", "euc-kr", "cp1252",
)

#: 最常见的汉字（简繁混排）：GBK 与 Big5 会**互相把对方的字节解成合法的汉字**，
#: 只能靠「解出来的是不是常用字」来判定，否则繁体课件会得到满屏生僻字。
_COMMON_HAN = frozenset(
    "的一是了我不人在他有这个上们来到时大地为子中你说生国年着就那和要她出也得里后自以会家"
    "可下而过天去能对小多然于心学么之都好看起发当没成只如事把还用第样道想作种开美总从无情"
    "己面最女但现前些所同日手又行意动方期它头经长儿回位分爱老因很给名法间斯知世什两次使身"
    "者被高已亲其进此话常与活正感课程序设内容结构数据算法网络算机操作系统编译原理数字逻辑"
    "电路实验报告作业考试复习重点章节练习题目解答讲义教材参考书目教师老同学成绩学分论文"
    "電腦網路複習範圍學習測驗課程講義作業實驗報告資料題目解答練習章節重點筆記老師同學成績"
    "資訊軟體硬體系統程式設計演算法結構資料庫編譯原理數位邏輯電路學期選課考試範圍講解"
)

#: 假名/谚文：中文文本里出现这些字符几乎一定是解错了编码
_KANA_HANGUL = ((0x3040, 0x30FF), (0xAC00, 0xD7AF))


def _text_score(value: str) -> int:
    """给一种解码结果打分：常用汉字最多分，生僻字扣分。

    用于在同一串字节能被多种中日韩编码解码时选出「最像人话」的那一种。
    几个关键取舍（都是实测出来的）：

    - cp1252 能解出任何字节，所以西欧重音字母只给 +1：否则任意 GBK 中文
      都会被「解成一串带重音的拉丁字母」抢走（26 分 vs 25 分）；
    - 非高频汉字给 0 分而不是 +1：解错编码时产出的正是这类生僻字
      （GBK 字节按 Big5 解 → 「數呾儂厙釐」），给分会让错误解码追平正确解码；
    - 假名/谚文给 +1（和拉丁字母同级）：中文文本里出现假名说明解错了，
      但日文文本里假名本来就是正文，不能一律重罚。
    """
    score = 0
    for ch in value[:4000]:
        code = ord(ch)
        if ch in _COMMON_HAN:
            score += 3
        elif 0x4E00 <= code <= 0x9FFF:
            score += 0          # 生僻汉字：既不加分也不扣分
        elif 0x3400 <= code <= 0x4DBF or code >= 0x20000:
            score -= 3          # 扩展区生僻字
        elif any(lo <= code <= hi for lo, hi in _KANA_HANGUL):
            score += 2          # 假名/谚文：本语言正文，且不可能是中文的解码副产物
        elif 0x00C0 <= code <= 0x024F:
            score += 1          # 拉丁字母重音字符（西欧文本）
        elif 0x20 <= code <= 0x7E:
            score += 1
    return score


def _strict_decode(data: bytes, encoding: str) -> str:
    """严格解码但**容忍末尾被截断的多字节序列**（增量解码器不 finalize 即丢弃尾巴）。

    大文件按字节上限截断后再解码，最后一个汉字往往被切掉一半；
    直接 `data.decode(enc)` 会抛 UnicodeDecodeError，从而误判成「不是这种编码」，
    最后退化成乱码。增量解码器会把不完整的尾部字节留在缓冲区里丢掉。
    """
    decoder = codecs.getincrementaldecoder(encoding)()
    return decoder.decode(data)


def decode_text(data: bytes) -> tuple[str, str]:
    """字节 → (文本, 编码名)：按编码阶梯逐个严格解码，全失败则 UTF-8 替换兜底。

    UTF-8 能严格解码就采信（多字节序列很难偶然合法）；其余东亚编码会同时
    「解得出但解错」，此时按 `_text_score` 选最像中文的那个。
    """
    if not data:
        return "", "utf-8"
    if data[:3] == b"\xef\xbb\xbf":
        try:
            return _strict_decode(data, "utf-8-sig"), "utf-8-sig"
        except UnicodeDecodeError:
            pass
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        try:
            return _strict_decode(data, "utf-16"), "utf-16"
        except UnicodeDecodeError:
            pass
    if max(data[:4096]) < 0x80:
        return data.decode("ascii", errors="replace"), "ascii"   # 纯 ASCII：任何编码都一样
    candidates: list[tuple[int, int, str, str]] = []
    for index, enc in enumerate(ENCODINGS):
        try:
            text = _strict_decode(data, enc)
        except (UnicodeDecodeError, LookupError):
            continue
        if enc == "utf-8":
            return text, enc
        candidates.append((_text_score(text), -index, text, enc))
    if candidates:
        candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
        best = candidates[0]
        return best[2], best[3]
    return data.decode("utf-8", errors="replace"), "utf-8"


def read_text(path: Path, limit: int = MAX_TEXT_CHARS, *, max_bytes: int | None = None) -> tuple[str, str]:
    """读文件前 limit 个字符 → (文本, 编码)。读取失败返回 ("", "")。

    **只从磁盘读 `limit*4` 字节**（UTF-8 一个汉字最多 3 字节，4 倍足够覆盖）：
    上传上限是 512MB，直接 read_bytes() 会把整个文件读进内存，而正文最终
    也只保留 20 万字符 —— 大文件必须在这里就截断。
    """
    cap = max_bytes if max_bytes is not None else limit * 4 + 8
    try:
        with path.open("rb") as handle:
            raw = handle.read(cap)
    except OSError:
        return "", ""
    text, enc = decode_text(raw)
    return text[:limit], enc


# ---------------------------------------------------------------------------
# 纯文本 / 代码 / 表格文本
# ---------------------------------------------------------------------------

def extract_plain_text(path: Path) -> ExtractedFile:
    """TXT / MD / TeX / 日志等纯文本。"""
    text, enc = read_text(path)
    if not text:
        return ExtractedFile("")
    result = ExtractedFile(clean_text(text))
    if enc not in ("utf-8", "utf-8-sig"):
        result.with_warning(f"文件按 {enc} 编码解析，若出现乱码请在编辑器里另存为 UTF-8 后重试")
    return result


def extract_code_text(path: Path) -> ExtractedFile:
    """代码/脚本：原文 + 顶层定义摘要（便于「这份代码讲了什么」的提炼）。"""
    text, enc = read_text(path)
    if not text:
        return ExtractedFile("")
    summary = _code_summary(text)
    body = clean_text(text)
    header = f"[代码文件] {text_slice(path.name, 120)}"
    if summary:
        header += "\n[顶层定义]\n" + summary
    if enc not in ("utf-8", "utf-8-sig"):
        header += f"\n[编码] {enc}"
    return ExtractedFile(clean_text(header + "\n\n" + body))


_DEF_PATTERNS = (
    re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)"),
    re.compile(r"^\s*class\s+([A-Za-z_]\w*)"),
    re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)"),
    re.compile(r"^\s*(?:public|private|protected|static|final|\s)*[\w<>\[\], ]+\s+([A-Za-z_]\w*)\s*\([^;]*\)\s*\{"),
    re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)"),
    re.compile(r"^\s*(?:fn|pub fn|impl)\s+([A-Za-z_]\w*)"),
)


def _code_summary(text: str) -> str:
    """抓顶层定义名（最多 MAX_CODE_SUMMARY 条），给 LLM 一个结构骨架。"""
    found: list[str] = []
    for line in text.split("\n")[:4000]:
        for pattern in _DEF_PATTERNS:
            m = pattern.match(line)
            if m and m.group(1) not in found:
                found.append(m.group(1))
                break
        if len(found) >= MAX_CODE_SUMMARY:
            break
    return "、".join(found)


def extract_table_text(path: Path) -> ExtractedFile:
    """CSV / TSV：渲染成「表头 + 行号」的对齐文本，而不是原样倒出分隔符。

    成绩单、点名册这类表格直接倒逗号，LLM 很难看出哪一列是名称、哪一列是日期；
    渲染成表格语义后，DDL/章节抽取的命中率明显更高。
    """
    text, enc = read_text(path, limit=2_000_000)
    if not text:
        return ExtractedFile("")
    sample = text[:4096]
    delimiter = "\t" if path.suffix.lower() == ".tsv" else None
    if delimiter is None:
        try:
            delimiter = csv.Sniffer().sniff(sample, delimiters=",\t;|").delimiter
        except csv.Error:
            delimiter = "\t" if sample.count("\t") > sample.count(",") else ","
    try:
        rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    except csv.Error:
        return ExtractedFile(clean_text(text))
    lines: list[str] = []
    header: list[str] = []
    for row in rows[: MAX_TABLE_ROWS + 1]:
        cells = [text_slice(c, 200) for c in row[:MAX_TABLE_COLS]]
        if not any(cells):
            continue
        if not header and not lines:
            header = cells
            continue
        lines.append(f"{len(lines) + 1}. " + " | ".join(cells))
    if not header and not lines:
        return ExtractedFile("")
    width = max(len(header), max((len(line.split(" | ")) for line in lines), default=0))
    head = f"[表格] {text_slice(path.name, 120)}；{width} 列 × {len(lines)} 行（编码 {enc}）"
    if header:
        head += "\n表头: " + " | ".join(header)
    if len(rows) > MAX_TABLE_ROWS + 1:
        head += f"\n（仅渲染前 {MAX_TABLE_ROWS} 行）"
    result = ExtractedFile(clean_text(head + "\n" + "\n".join(lines)))
    if enc not in ("utf-8", "utf-8-sig", "ascii"):
        result.with_warning(f"表格按 {enc} 编码解析，若出现乱码请另存为 UTF-8 后重试")
    return result


# ---------------------------------------------------------------------------
# 网页
# ---------------------------------------------------------------------------

_SKIP_TAGS = {"script", "style", "noscript", "template", "svg", "head"}
_BLOCK_TAGS = {
    "p", "div", "section", "article", "header", "footer", "li", "tr", "br",
    "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre", "table", "ul", "ol",
}


class _HtmlTextExtractor(HTMLParser):
    """HTML → 纯文本：保留标题/段落/列表/表格的语义，丢掉脚本与样式。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self._skip_depth = 0
        self._in_title = False
        self._row_cells = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if tag == "title":
            self._in_title = True
        elif tag == "tr":
            self._row_cells = 0
            self.parts.append("\n")
        elif tag in ("td", "th"):
            if self._row_cells:
                self.parts.append(" | ")
            self._row_cells += 1
        elif tag == "br":
            self.parts.append("\n")
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")
        elif tag == "img":
            alt = dict(attrs).get("alt") or ""
            if alt.strip():
                self.parts.append(f"[图片: {text_slice(_html.unescape(alt), 80)}]")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if tag == "title":
            self._in_title = False
        elif tag in _BLOCK_TAGS and tag != "tr":
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self.title += data
            return
        if data.strip() or (self.parts and self.parts[-1].endswith(" ")):
            self.parts.append(data)


def html_to_text(markup: str, limit: int = MAX_TEXT_CHARS) -> tuple[str, str]:
    """HTML 字符串 → (正文, 标题)。解析失败时退化为「去标签」的粗抽取。"""
    parser = _HtmlTextExtractor()
    try:
        parser.feed(markup[: limit * 4])
        parser.close()
    except Exception:  # HTMLParser 对畸形文档一般很宽容，这里只兜底
        return clean_text(_strip_tags(markup), limit), ""
    body = clean_text("".join(parser.parts), limit)
    return body, text_slice(_html.unescape(parser.title), 200)


_TAG_RE = re.compile(r"<[^>]{0,2000}>")
_SCRIPT_RE = re.compile(r"<(script|style)\b.*?</\1>", re.S | re.I)


def _strip_tags(markup: str) -> str:
    return _html.unescape(_TAG_RE.sub(" ", _SCRIPT_RE.sub(" ", markup)))


def extract_html_text(path: Path) -> ExtractedFile:
    """HTML / XHTML 网页：抽出标题与正文（含表格单元格）。"""
    text, enc = read_text(path, limit=MAX_TEXT_CHARS * 4)
    if not text:
        return ExtractedFile("")
    body, title = html_to_text(text)
    if not body:
        return ExtractedFile("", warnings=["网页里没有抽取到正文，可能是纯脚本页面"])
    head = f"[网页] {title or text_slice(path.name, 120)}"
    result = ExtractedFile(clean_text(head + "\n" + body))
    if enc not in ("utf-8", "utf-8-sig"):
        result.with_warning(f"网页按 {enc} 编码解析")
    return result


# ---------------------------------------------------------------------------
# RTF
# ---------------------------------------------------------------------------

#: 这些目标组里全是格式定义，不是正文，进组后直接跳到匹配的右括号
_RTF_SKIP_DESTINATIONS = {
    "fonttbl", "colortbl", "stylesheet", "info", "pict", "object", "themedata",
    "datastore", "latentstyles", "listtable", "listoverridetable", "rsidtbl",
    "generator", "filetbl", "revtbl", "xmlnstbl", "shppict", "nonshppict",
    "header", "footer", "headerl", "headerr", "footerl", "footerr", "ftnsep",
    "upr", "bkmkstart", "bkmkend", "fldinst", "pgptbl", "pnseclvl",
}
_RTF_CODEPAGES = {936: "gbk", 950: "big5", 65001: "utf-8", 932: "shift_jis",
                  949: "euc-kr", 1252: "cp1252", 1251: "cp1251", 1250: "cp1250"}
_RTF_CTRL_WORD = re.compile(r"([a-zA-Z]+)(-?\d+)? ?")


def extract_rtf_text(path: Path) -> ExtractedFile:
    """RTF 富文本 → 纯文本（支持 \\'hh 十六进制转义、\\uN Unicode 转义与 \\ansicpg 代码页）。"""
    try:
        # RTF 是纯 ASCII 源码，1 字符 = 1 字节，按 4 倍上限读足够（大文件不整载入）
        with path.open("rb") as handle:
            raw = handle.read(MAX_TEXT_CHARS * 4)
    except OSError:
        return ExtractedFile("")
    if not raw.strip().startswith(b"{\\rtf"):
        return ExtractedFile("")
    head = raw[:4096].decode("latin-1", errors="ignore")
    m = re.search(r"\\ansicpg(\d+)", head)
    encoding = _RTF_CODEPAGES.get(int(m.group(1)), "cp1252") if m else "cp1252"
    text = _rtf_to_text(raw.decode("latin-1", errors="ignore"), encoding)
    if not text.strip():
        return ExtractedFile("", warnings=["RTF 里没有抽取到正文文本"])
    return ExtractedFile(clean_text(f"[RTF 文档] {text_slice(path.name, 120)}\n{text}"))


def _rtf_to_text(source: str, encoding: str) -> str:
    out: list[str] = []
    pending = bytearray()          # 连续的 \'hh 字节攒起来一起按代码页解码
    i, depth = 0, 0
    skip_depth = -1                # >=0 表示正处于要跳过的目标组
    uc_skip = 1                    # \ucN：\uN 之后需要跳过的回退字符数
    n = len(source)

    def flush() -> None:
        if pending:
            out.append(bytes(pending).decode(encoding, errors="replace"))
            pending.clear()

    while i < n:
        ch = source[i]
        if ch == "{":
            depth += 1
            i += 1
            continue
        if ch == "}":
            flush()
            depth -= 1
            if skip_depth >= 0 and depth <= skip_depth:
                skip_depth = -1
            i += 1
            continue
        if ch != "\\":
            if skip_depth < 0:
                flush()
                out.append(ch)
            i += 1
            continue
        i += 1
        if i >= n:
            break
        ctrl = source[i]
        if ctrl == "*":            # \*\destination：可忽略的目标组
            if skip_depth < 0:
                skip_depth = max(0, depth - 1)
            i += 1
            continue
        if ctrl in "\\{}":         # 转义的字面字符
            if skip_depth < 0:
                flush()
                out.append(ctrl)
            i += 1
            continue
        if ctrl == "'":            # \'hh 十六进制字节
            hex_part = source[i + 1: i + 3]
            if len(hex_part) == 2 and all(c in "0123456789abcdefABCDEF" for c in hex_part):
                if skip_depth < 0:
                    pending.append(int(hex_part, 16))
                i += 3
            else:
                i += 1
            continue
        if ctrl in "~-_":
            if skip_depth < 0:
                flush()
                out.append({"~": "\u00a0", "-": "", "_": "-"}[ctrl])
            i += 1
            continue
        if ctrl == "\n" or ctrl == "\r":
            i += 1
            continue
        m = _RTF_CTRL_WORD.match(source, i)
        if not m:
            i += 1
            continue
        word, param = m.group(1).lower(), m.group(2)
        i = m.end()
        if skip_depth >= 0:
            continue
        if word == "uc" and param is not None:
            try:
                uc_skip = max(0, min(8, int(param)))
            except ValueError:
                uc_skip = 1
            continue
        if word in _RTF_SKIP_DESTINATIONS:
            skip_depth = depth - 1
            continue
        if word in ("par", "line", "sect", "page", "pard"):
            flush()
            out.append("\n")
            continue
        if word == "tab":
            flush()
            out.append("\t")
            continue
        if word == "u" and param is not None:
            code = int(param)
            if code < 0:
                code += 65536
            flush()
            out.append(chr(code))
            # \uN 后面跟着 uc_skip 个回退字符（通常是 '?'），要跳过
            skipped, j = 0, i
            while skipped < uc_skip and j < n:
                if source[j] == "\\":
                    m2 = re.match(r"\\'(?:[0-9a-fA-F]{2})|\\[a-zA-Z]+-?\d* ?|\\[\\{}]", source[j:])
                    if m2:
                        j += m2.end()
                    else:
                        j += 1
                else:
                    j += 1
                skipped += 1
            i = j
            continue
        if word in ("ansicpg", "deff", "fs", "f", "cf", "highlight", "b", "i", "ul",
                    "strike", "super", "sub", "v", "expnd", "expndtw", "outl", "rtlch",
                    "ltrch", "loch", "hich", "dbch", "cgrid", "lang", "langfe", "langnp",
                    "insrsid", "chftn", "chdate", "chtime", "chpgn", "cell", "row",
                    "intbl", "trowd", "trgaph", "clvertalt", "brdrw", "brdrs", "qc", "ql",
                    "qr", "qj"):
            if word in ("cell", "row"):
                flush()
                out.append("\t" if word == "cell" else "\n")
            continue
        # 其余未知控制字直接忽略（RTF 规范允许）

    flush()
    text = "".join(out).replace("\u00a0", " ")
    text = re.sub(r"[ \t]+\n", "\n", text)
    return text


# ---------------------------------------------------------------------------
# Jupyter Notebook
# ---------------------------------------------------------------------------

def extract_ipynb_text(path: Path) -> ExtractedFile:
    """Jupyter Notebook → Markdown 正文 + 代码 + 文本输出。"""
    text, _ = read_text(path, limit=MAX_TEXT_CHARS * 8)
    if not text:
        return ExtractedFile("")
    try:
        nb = json.loads(text)
    except ValueError:
        return ExtractedFile("", warnings=["Notebook 不是有效的 JSON，无法解析"])
    if not isinstance(nb, dict):
        return ExtractedFile("", warnings=["Notebook 结构异常，无法解析"])
    cells = nb.get("cells")
    if not isinstance(cells, list):
        return ExtractedFile("", warnings=["Notebook 里没有单元格"])
    blocks: list[str] = []
    for index, cell in enumerate(cells[:MAX_NOTEBOOK_CELLS], 1):
        if not isinstance(cell, dict):
            continue
        source = cell.get("source")
        body = "".join(source) if isinstance(source, list) else str(source or "")
        kind = cell.get("cell_type")
        if kind == "markdown":
            if body.strip():
                blocks.append(body.strip())
        elif kind == "code":
            section = f"[代码 {index}]\n{body.strip()}"
            outputs = cell.get("outputs")
            rendered = _notebook_outputs(outputs)
            if rendered:
                section += f"\n[输出 {index}]\n{rendered}"
            if body.strip() or rendered:
                blocks.append(section)
        elif kind == "raw" and body.strip():
            blocks.append(body.strip())
    if not blocks:
        return ExtractedFile("", warnings=["Notebook 里没有可抽取的文本内容"])
    head = f"[Jupyter Notebook] {text_slice(path.name, 120)}；共 {len(cells)} 个单元格"
    return ExtractedFile(clean_text(head + "\n\n" + "\n\n".join(blocks)))


def _notebook_outputs(outputs) -> str:
    """Notebook 单元格输出 → 文本（图片/HTML 等二进制输出只留标记）。"""
    if not isinstance(outputs, list):
        return ""
    parts: list[str] = []
    for out in outputs[:20]:
        if not isinstance(out, dict):
            continue
        out_type = out.get("output_type")
        if out_type == "stream":
            chunk = out.get("text")
            parts.append("".join(chunk) if isinstance(chunk, list) else str(chunk or ""))
            continue
        if out_type in ("execute_result", "display_data"):
            data = out.get("data")
            if isinstance(data, dict):
                plain = data.get("text/plain")
                if plain is not None:
                    parts.append("".join(plain) if isinstance(plain, list) else str(plain))
                elif "image/png" in data:
                    parts.append("[图片输出]")
            continue
        if out_type == "error":
            name, value = out.get("ename") or "", out.get("evalue") or ""
            parts.append(f"[报错] {name}: {value}")
    return clean_text("\n".join(p for p in parts if p and p.strip()), 20_000)


# ---------------------------------------------------------------------------
# 字幕 / 听记
# ---------------------------------------------------------------------------

_SRT_TIME = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})[.,](\d{1,3})")
_VTT_HEADER = re.compile(r"^\s*WEBVTT", re.I)
_ASS_DIALOGUE = re.compile(r"^Dialogue:[^,]*,[^,]*,[^,]*,[^,]*,[^,]*,[^,]*,[^,]*,[^,]*,[^,]*,(.*)$")
_ASS_TAGS = re.compile(r"\{[^}]{0,200}\}")
_LRC_TIME = re.compile(r"^\[(\d{1,2}):(\d{2})(?:[.:](\d{1,2}))?\]")


def _stamp(hours: str, minutes: str, seconds: str, frac: str = "") -> str:
    total = int(hours) * 3600 + int(minutes) * 60 + int(seconds)
    if total >= 3600:
        return f"{total // 3600:02d}:{total % 3600 // 60:02d}:{total % 60:02d}"
    return f"{total // 60:02d}:{total % 60:02d}"


def extract_subtitle_text(path: Path) -> ExtractedFile:
    """SRT / VTT / ASS·SSA / LRC → 带时间戳的听记文本（时间戳保留，便于定位考点）。"""
    text, enc = read_text(path, limit=MAX_TEXT_CHARS * 4)
    if not text.strip():
        return ExtractedFile("")
    ext = path.suffix.lower()
    if ext in (".ass", ".ssa"):
        lines = _ass_lines(text)
    elif ext == ".lrc":
        lines = _lrc_lines(text)
    else:
        lines = _cue_lines(text)
    if not lines:
        return ExtractedFile("", warnings=["字幕文件里没有识别到台词"])
    head = f"[字幕/听记] {text_slice(path.name, 120)}；{len(lines)} 条（编码 {enc}）"
    return ExtractedFile(clean_text(head + "\n" + "\n".join(lines)))


def _cue_lines(text: str) -> list[str]:
    """SRT/VTT：按「时间行 + 紧跟的台词」对齐（空行即一条字幕结束）。"""
    lines: list[str] = []
    current: str | None = None
    buffer: list[str] = []

    def flush() -> None:
        nonlocal current, buffer
        if current and buffer:
            lines.append(f"[{current}] " + " ".join(buffer))
        current, buffer = None, []

    for raw in _VTT_HEADER.sub("", text).split("\n"):
        line = raw.strip()
        if not line:
            flush()
            continue
        if line.upper().startswith(("NOTE", "STYLE", "REGION")):
            continue
        m = _SRT_TIME.search(line)
        if m:
            flush()
            current = _stamp(*m.groups())
            continue
        if current is None:
            continue                      # 时间行之前的序号/元信息一律丢弃
        buffer.append(_html.unescape(line))
    flush()
    return lines[:MAX_SUBTITLE_LINES]


def _ass_lines(text: str) -> list[str]:
    """ASS/SSA：Dialogue 行第 10 个字段是台词，\\N 是换行，{...} 是特效标签。"""
    lines: list[str] = []
    for raw in text.split("\n"):
        if not raw.startswith("Dialogue:"):
            continue
        m = _ASS_DIALOGUE.match(raw.strip())
        if not m:
            continue
        body = _ASS_TAGS.sub("", m.group(1)).replace("\\N", " ").replace("\\n", " ")
        body = " ".join(body.split())
        if body:
            lines.append(body)
    return lines[:MAX_SUBTITLE_LINES]


def _lrc_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.split("\n"):
        m = _LRC_TIME.match(raw.strip())
        if not m:
            continue
        body = raw.strip()[m.end():].strip()
        if body and not body.startswith("["):        # 跳过 [ti:] [ar:] 这类元信息
            lines.append(f"[{_stamp('0', m.group(1), m.group(2), m.group(3) or '')}] {body}")
    return lines[:MAX_SUBTITLE_LINES]


# ---------------------------------------------------------------------------
# 结构化数据
# ---------------------------------------------------------------------------

def extract_json_text(path: Path) -> ExtractedFile:
    """JSON：先给一份结构摘要（顶层键/数组长度），再给格式化正文。"""
    text, enc = read_text(path, limit=MAX_TEXT_CHARS * 4)
    if not text:
        return ExtractedFile("")
    try:
        data = json.loads(text)
    except ValueError:
        return ExtractedFile(clean_text(f"[JSON 文本（未能解析）] {text_slice(path.name, 120)}\n{text}"))
    summary = _json_summary(data)
    body = json.dumps(data, ensure_ascii=False, indent=2)
    head = f"[JSON] {text_slice(path.name, 120)}（编码 {enc}）\n[结构] {summary}"
    return ExtractedFile(clean_text(head + "\n" + body))


def _json_summary(data, depth: int = 0) -> str:
    """一层结构摘要：键名 + 容器类型/长度（不递归展开，避免摘要比正文还长）。"""
    if isinstance(data, dict):
        parts = []
        for key, value in list(data.items())[:20]:
            if isinstance(value, list):
                parts.append(f"{key}[{len(value)}]")
            elif isinstance(value, dict):
                parts.append(f"{key}{{{len(value)}}}")
            else:
                parts.append(str(key))
        return "、".join(parts) or "空对象"
    if isinstance(data, list):
        return f"数组，共 {len(data)} 项"
    return type(data).__name__


def extract_yaml_text(path: Path) -> ExtractedFile:
    """YAML：能解析就转成规整缩进文本（注释会丢，但结构更清晰），否则原文。"""
    text, enc = read_text(path, limit=MAX_TEXT_CHARS * 4)
    if not text:
        return ExtractedFile("")
    head = f"[YAML] {text_slice(path.name, 120)}（编码 {enc}）"
    try:
        import yaml  # 可选依赖：装了才做结构化渲染

        data = yaml.safe_load(text)
        rendered = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False)
        return ExtractedFile(clean_text(head + "\n" + rendered))
    except Exception:
        return ExtractedFile(clean_text(head + "\n" + text))


def extract_xml_text(path: Path) -> ExtractedFile:
    """XML：优先按元素抽「标签: 文本」，解析失败则去标签取正文。"""
    text, enc = read_text(path, limit=MAX_TEXT_CHARS * 4)
    if not text:
        return ExtractedFile("")
    head = f"[XML] {text_slice(path.name, 120)}（编码 {enc}）"
    try:
        from xml.etree import ElementTree

        root = ElementTree.fromstring(text[:MAX_TEXT_CHARS * 4])
    except Exception:
        return ExtractedFile(clean_text(head + "\n" + _strip_tags(text)))
    lines: list[str] = []
    stack = [(root, 0)]
    visited = 0
    while stack and visited < 5000:
        node, depth = stack.pop()
        visited += 1
        tag = node.tag.rsplit("}", 1)[-1] if isinstance(node.tag, str) else "?"
        # 文本行合并展示，避免一个字段一行把 200k 字吃光
        for key, value in list(node.attrib.items())[:10]:
            lines.append(f"{'  ' * min(depth, 8)}{tag}@{key.rsplit('}', 1)[-1]} = {text_slice(str(value), 200)}")
        own = (node.text or "").strip()
        if own:
            lines.append(f"{'  ' * min(depth, 8)}{tag}: {text_slice(own, 500)}")
        for child in reversed(list(node)):
            stack.append((child, depth + 1))
    if not lines:
        return ExtractedFile(clean_text(head + "\n" + _strip_tags(text)))
    return ExtractedFile(clean_text(head + "\n" + "\n".join(lines)))


__all__ = [
    "ENCODINGS", "decode_text", "extract_code_text", "extract_html_text",
    "extract_ipynb_text", "extract_json_text", "extract_plain_text",
    "extract_rtf_text", "extract_subtitle_text", "extract_table_text",
    "extract_xml_text", "extract_yaml_text", "html_to_text", "join_blocks",
    "read_text",
]
