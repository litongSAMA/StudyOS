"""电子书抽取：EPUB（ZIP + XHTML）与 MOBI/AZW3（PalmDB + PalmDOC 压缩）。

电子书是「章节化」的长文本，抽取时保留章顺序很重要：
EPUB 按 OPF spine 的阅读顺序取正文，MOBI 按记录顺序解压后去标签。
两类文件都是**不可信输入**：记录数、解压后体积、章节数、章节大小全部有上限，
PalmDOC 的「距离/长度」也要夹紧，避免一个构造出来的文件把内存吃干净。
"""
from __future__ import annotations

import zipfile
from pathlib import Path
from xml.etree import ElementTree

from app.infra.ingest.base import ExtractedFile, clean_text, join_blocks, text_slice
from app.infra.ingest.textfmt import html_to_text

MAX_TEXT_CHARS = 200_000
MAX_EPUB_CHAPTERS = 300
MAX_CHAPTER_BYTES = 2 * 1024 * 1024
MAX_MOBI_RECORDS = 20_000
MAX_MOBI_BYTES = 8 * 1024 * 1024
MAX_MOBI_READ = 32 * 1024 * 1024   # 单次读入上限（记录偏移越界的部分直接跳过）

_CONTAINER_XML = "META-INF/container.xml"
_XHTML_SUFFIXES = (".xhtml", ".html", ".htm")


# ---------------------------------------------------------------------------
# EPUB
# ---------------------------------------------------------------------------

def extract_epub(path: Path) -> ExtractedFile:
    """EPUB：按 OPF spine 顺序抽取各章正文（无 OPF 时按文件名顺序兜底）。"""
    try:
        with zipfile.ZipFile(path) as zf:
            names = [n for n in zf.namelist() if not n.endswith("/")]
            ordered = _epub_spine(zf, names)
            if not ordered:
                ordered = sorted(n for n in names if n.lower().endswith(_XHTML_SUFFIXES))
            blocks: list[str] = []
            total = 0
            for index, name in enumerate(ordered[:MAX_EPUB_CHAPTERS], 1):
                if total >= MAX_TEXT_CHARS:
                    break
                body = _epub_chapter(zf, name)
                if not body:
                    continue
                blocks.append(f"[第{index}章] {body}")
                total += len(body)
    except (zipfile.BadZipFile, OSError, KeyError, EOFError):
        return ExtractedFile("", warnings=["EPUB 无法读取，可能已损坏或不是有效的电子书"])
    if not blocks:
        return ExtractedFile("", warnings=["EPUB 里没有抽取到正文，可能是纯图片电子书"])
    return ExtractedFile(join_blocks(blocks))


def _epub_spine(zf: zipfile.ZipFile, names: list[str]) -> list[str]:
    """读 container.xml → OPF → spine 顺序的正文文件（解析失败返回 []）。"""
    try:
        if _CONTAINER_XML not in names:
            return []
        root = ElementTree.fromstring(zf.read(_CONTAINER_XML))
        ref = None
        for node in root.iter():
            if node.tag.rsplit("}", 1)[-1] == "rootfile":
                ref = node.get("full-path")
                break
        if not ref or ref not in names:
            return []
        opf = ElementTree.fromstring(zf.read(ref))
    except (ElementTree.ParseError, KeyError, OSError):
        return []
    manifest: dict[str, str] = {}
    spine_ids: list[str] = []
    for node in opf.iter():
        tag = node.tag.rsplit("}", 1)[-1]
        if tag == "item":
            item_id, href = node.get("id"), node.get("href")
            if item_id and href:
                manifest[item_id] = href
        elif tag == "itemref":
            idref = node.get("idref")
            if idref:
                spine_ids.append(idref)
    base = ref.rsplit("/", 1)[0] if "/" in ref else ""
    ordered: list[str] = []
    for idref in spine_ids:
        href = manifest.get(idref)
        if not href:
            continue
        full = _join_zip_path(base, href)
        if full in names and full.lower().endswith(_XHTML_SUFFIXES):
            ordered.append(full)
    return ordered


def _join_zip_path(base: str, href: str) -> str:
    """OPF 内的相对 href → ZIP 条目名（处理 ../ 与 %20 转义）。"""
    from urllib.parse import unquote

    href = unquote(href.split("#", 1)[0])
    parts = [p for p in (base.split("/") if base else []) if p]
    for piece in href.split("/"):
        if piece in ("", "."):
            continue
        if piece == "..":
            if parts:
                parts.pop()
            continue
        parts.append(piece)
    return "/".join(parts)


def _epub_chapter(zf: zipfile.ZipFile, name: str) -> str:
    """单章 XHTML → 正文（超长章节截断，防止单章吃满预算）。"""
    try:
        info = zf.getinfo(name)
        if info.file_size > MAX_CHAPTER_BYTES:
            return ""
        raw = zf.read(name)
    except (KeyError, OSError, zipfile.BadZipFile):
        return ""
    from app.infra.ingest.textfmt import decode_text

    markup, _ = decode_text(raw)
    body, title = html_to_text(markup, limit=MAX_TEXT_CHARS)
    if title and title not in body[:200]:
        return clean_text(f"{title}\n{body}", 60_000)
    return clean_text(body, 60_000)


# ---------------------------------------------------------------------------
# MOBI / AZW3
# ---------------------------------------------------------------------------

_PALMDOC_HEADER = 16
_PALMDB_HEADER = 78


def extract_mobi(path: Path) -> ExtractedFile:
    """MOBI/AZW3：PalmDB 记录 → PalmDOC 解压 → 去 HTML 标签取正文。

    只支持无压缩(1)与 PalmDOC 压缩(2)；Kindle 的 HUFF/CDIC(17480) 需要额外字典，
    这里明确告知用户转换，而不是给出一堆乱码。
    """
    try:
        # 只读前 MAX_MOBI_READ 字节：记录偏移全在文件内部，越界的条目会被跳过，
        # 不能让一个假的 512MB「电子书」把整个文件读进内存。
        with path.open("rb") as handle:
            data = handle.read(MAX_MOBI_READ)
    except OSError:
        return ExtractedFile("")
    if len(data) < _PALMDB_HEADER + _PALMDOC_HEADER:
        return ExtractedFile("", warnings=["电子书文件不完整，无法解析"])
    record_count = int.from_bytes(data[76:78], "big")
    if not 1 <= record_count <= MAX_MOBI_RECORDS:
        return ExtractedFile("", warnings=["电子书结构异常（记录数越界），无法解析"])
    offsets: list[int] = []
    for index in range(record_count):
        start = _PALMDB_HEADER + index * 8
        if start + 4 > len(data):
            break
        offsets.append(int.from_bytes(data[start:start + 4], "big"))
    if len(offsets) < 2:
        return ExtractedFile("", warnings=["电子书里没有正文记录"])
    offsets.append(len(data))
    text_length = int.from_bytes(data[offsets[0] + 4: offsets[0] + 8], "big")
    compression = int.from_bytes(data[offsets[0]: offsets[0] + 2], "big")
    if compression == 17480:
        return ExtractedFile(
            "", warnings=["这是 Kindle 的 HUFF/CDIC 压缩电子书，本地无法解析；请用 Calibre 转成 EPUB 后重试"]
        )
    if compression not in (1, 2):
        return ExtractedFile("", warnings=["电子书使用了未知的压缩方式，无法解析"])
    chunks: list[bytes] = []
    total = 0
    for index in range(1, len(offsets) - 1):
        start, end = offsets[index], offsets[index + 1]
        if not 0 <= start < end <= len(data):
            continue
        chunk = data[start:end]
        if compression == 2:
            chunk = _palmdoc_decompress(chunk)
        chunks.append(chunk)
        total += len(chunk)
        if total >= MAX_MOBI_BYTES:
            break
    raw = b"".join(chunks)[: max(text_length, 1) if text_length else MAX_MOBI_BYTES]
    from app.infra.ingest.textfmt import decode_text

    markup, _ = decode_text(raw[:MAX_MOBI_BYTES])
    body, title = html_to_text(markup, limit=MAX_TEXT_CHARS)
    if not body:
        return ExtractedFile("", warnings=["电子书里没有抽取到正文，可能是纯图片电子书"])
    head = f"[电子书] {text_slice(title or path.stem, 160)}"
    return ExtractedFile(clean_text(head + "\n" + body))


def _palmdoc_decompress(data: bytes) -> bytes:
    """PalmDOC LZ77 解压（带越界保护：距离超出已输出范围时按字面量处理）。"""
    out = bytearray()
    index = 0
    length = len(data)
    while index < length:
        byte = data[index]
        index += 1
        if byte == 0x00:
            out.append(0)
        elif 0x01 <= byte <= 0x08:
            out.extend(data[index:index + byte])
            index += byte
        elif byte <= 0x7F:
            out.append(byte)
        elif byte <= 0xBF:
            if index >= length:
                break
            pair = (byte << 8) | data[index]
            index += 1
            distance = (pair >> 3) & 0x07FF
            copy_len = (pair & 0x07) + 3
            if distance == 0 or distance > len(out):
                continue
            start = len(out) - distance
            for offset in range(copy_len):
                if start + offset < len(out):
                    out.append(out[start + offset])
            if len(out) > MAX_MOBI_BYTES:
                break
        else:
            out.append(0x20)
            out.append(byte ^ 0x80)
        if len(out) > MAX_MOBI_BYTES:
            break
    return bytes(out)


__all__ = ["extract_epub", "extract_mobi"]
