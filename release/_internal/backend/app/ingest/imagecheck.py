"""图片尺寸安全校验：OCR 之前拦截解压炸弹（decompression bomb）。

字节数上限（materials 512MB / notes 20MB）拦不住高压缩率的图片：
几十 KB 的 PNG/GIF 文件头就能声明 20000×20000，解码后占用 GB 级内存，
直接交给 rapidocr / PyMuPDF 会 OOM。这里用 Pillow 只读文件头
（惰性 open，不 decode 像素），先校验宽高与总像素，超限直接拒绝。
"""
from __future__ import annotations

import re
import warnings
from pathlib import Path

from fastapi import HTTPException
from PIL import Image, UnidentifiedImageError

MAX_IMAGE_SIDE = 12_000  # 单边像素上限（≤ 12000px）
MAX_IMAGE_PIXELS = 50_000_000  # 总像素上限（≤ 5000 万，约 200MB RGBA 解压后）

# 超过 Pillow 内置炸弹阈值（约 1.79 亿像素）的图片在 open 时直接抛
# DecompressionBombError，这里从异常信息中抠出像素数用于给用户精确提示。
_BOMB_PIXELS_RE = re.compile(r"Image size \((\d+) pixels\)")


def check_image_size(path: Path, ext: str = "") -> None:
    """只读 header 校验图片宽高；超限抛 413，损坏/非图片抛 422。

    `ext` 用于把「本机缺解码器」这类环境问题说清楚：HEIC/HEIF 在没装
    pillow-heif 的机器上会被 Pillow 判为无法识别，直接报「不是有效的图片」
    会让人以为是文件坏了，实际只需转成 JPG/PNG。
    """
    try:
        with warnings.catch_warnings():
            # 50M~89M 像素区间 Pillow 只发警告，反正都会被下面拒掉，不必刷日志
            warnings.simplefilter("ignore", Image.DecompressionBombWarning)
            with Image.open(path) as im:
                width, height = im.size
    except Image.DecompressionBombError as e:
        m = _BOMB_PIXELS_RE.search(str(e))
        pixels = int(m.group(1)) if m else 0
        if pixels:
            raise HTTPException(
                413,
                f"图片像素过多（约 {pixels // 10_000} 万像素），"
                f"上限 {MAX_IMAGE_PIXELS // 10_000} 万像素，请缩小后重试",
            ) from None
        raise HTTPException(413, "图片尺寸过大（疑似解压炸弹），请压缩后重试") from None
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
        raise HTTPException(422, _broken_message(path, ext)) from None
    if width <= 0 or height <= 0:
        raise HTTPException(422, "无法识别的图片文件：宽高非法")
    if width > MAX_IMAGE_SIDE or height > MAX_IMAGE_SIDE:
        raise HTTPException(
            413,
            f"图片尺寸过大（{width}×{height}），单边不能超过 {MAX_IMAGE_SIDE}px，请缩小后重试",
        )
    if width * height > MAX_IMAGE_PIXELS:
        raise HTTPException(
            413,
            f"图片像素过多（{width}×{height}，约 {width * height // 10_000} 万像素），"
            f"上限 {MAX_IMAGE_PIXELS // 10_000} 万像素，请缩小后重试",
        )


#: 需要额外解码器才能读的图片格式（Pillow 本体不含）→ 给「先转换」的指引
_EXTRA_DECODER_HINT = {
    ".heic": "HEIC/HEIF 需要额外的解码器，本机未安装；请在手机或图片工具里导出为 JPG/PNG 后重试",
    ".heif": "HEIC/HEIF 需要额外的解码器，本机未安装；请在手机或图片工具里导出为 JPG/PNG 后重试",
}


def _broken_message(path: Path, ext: str) -> str:
    """无法解码时的提示：区分「需要额外解码器」与「文件确实损坏」。"""
    normalized = (ext or path.suffix or "").lower()
    hint = _EXTRA_DECODER_HINT.get(normalized)
    if hint:
        return hint
    return "无法识别的图片文件：文件已损坏或不是有效的图片"
