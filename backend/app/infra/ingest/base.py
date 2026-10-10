"""抽取结果的公共类型与文本整理工具。

各抽取器（office / textfmt / ebook / archive / OCR）都返回同一个
`ExtractedFile`，调度层 `files.py` 只认这个类型，因此新增格式不会影响调用方。
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

#: 正文字符上限：与 OCR 原文上限一致（200k 字 ≈ 一本教材的一章）
MAX_TEXT_CHARS = 200_000

_ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\ufeff\u2060"), None)
# 控制字符（保留 \n \t），OCR/PDF 抽取常混入 \x0c 分页符之类
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_BLANKS = re.compile(r"\n{3,}")


@dataclass
class ExtractedFile:
    """一次文件文本抽取的结果。

    `needs_ocr` 表示「这个文件看得出来有内容，但当前通道抽不到文本」
    （扫描版 PDF、纯图片页），调用方据此决定是否走 OCR 或提示人工补充。
    """

    text: str = ""
    needs_ocr: bool = False
    pages: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def chars(self) -> int:
        return len(self.text)

    def with_warning(self, message: str) -> "ExtractedFile":
        if message and message not in self.warnings:
            self.warnings.append(message)
        return self


def clean_text(text: str, limit: int = MAX_TEXT_CHARS) -> str:
    """统一换行、去零宽与控制字符、压掉多余空行，最后截断到上限。

    PDF/OCR 抽出来的文本常带 \\r\\n、零宽空格与连续空行，
    直接喂给 DDL 抽取与 LLM 会稀释时间表达式的密度，先在这里统一口径。
    """
    if not text:
        return ""
    value = text.replace("\r\n", "\n").replace("\r", "\n")
    value = value.translate(_ZERO_WIDTH)
    value = _CONTROL.sub("", value)
    value = unicodedata.normalize("NFC", value)
    value = "\n".join(line.rstrip() for line in value.split("\n"))
    value = _BLANKS.sub("\n\n", value).strip()
    return value[:limit]


def join_blocks(blocks: list[str], limit: int = MAX_TEXT_CHARS) -> str:
    """把多个文本块拼起来（去掉空块，块间空一行）。"""
    parts = [b.strip() for b in blocks if b and b.strip()]
    return clean_text("\n\n".join(parts), limit)


def text_slice(value: str, limit: int) -> str:
    """短字段截断（标题、单元格等），超长时加省略号，避免半截词看着像完整值。"""
    value = (value or "").strip()
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"
