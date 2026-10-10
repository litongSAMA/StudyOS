"""OCR 通道（可选依赖）：截图 → 文本。

rapidocr-onnxruntime 未安装或当前 Python 无轮子时自动关闭，
不影响其余功能（配置 ocr_enabled=False 亦关闭）。
"""
from __future__ import annotations

import re
from pathlib import Path

from app.infra.config import settings

#: 与 files.py 的正文上限保持一致（OCR 结果同样受 200k 字约束）
MAX_OCR_CHARS = 200_000
#: 扫描版 PDF 的 OCR 页数上限（一页 ≈ 2~4 秒，超过就不适合在上传请求里同步做）
PDF_OCR_MAX_PAGES = 10

_ocr = None
_tried = False


def ocr_available() -> bool:
    global _ocr, _tried
    if not settings.ocr_enabled:
        return False
    if not _tried:
        _tried = True
        try:
            from rapidocr_onnxruntime import RapidOCR  # noqa: F401

            _ocr = RapidOCR()
        except Exception:
            _ocr = None
    return _ocr is not None


def _cleanup(text: str) -> str:
    """OCR 后处理：合并 CJK/数字之间的空格（'1 2 月1 8 日' → '12月18日'）。"""
    text = re.sub(r"(?<=[\d\u4e00-\u9fff])\s+(?=[\d\u4e00-\u9fff])", "", text)
    return text


def ocr_image(path: Path) -> str:
    """图片/扫描件 OCR，失败返回空串。"""
    if not ocr_available():
        return ""
    try:
        result, _ = _ocr(str(path))
        if not result:
            return ""
        return _cleanup("\n".join(line[1] for line in result))
    except Exception:
        return ""


def ocr_pdf(path: Path, *, max_pages: int = PDF_OCR_MAX_PAGES) -> str:
    """扫描版 PDF → OCR 文本：逐页栅格化后识别（页数有上限，临时文件必定清理）。

    为什么不用 PDF 内嵌图直接 OCR：一页可能是多张图拼版（左文右图），
    整页栅格化能保留版面顺序，OCR 出来的阅读顺序更接近原文。
    """
    if not ocr_available():
        return ""
    from app.infra.ingest.office import cleanup_dir, pdf_pages_to_images

    tmp_dir, images = pdf_pages_to_images(path, max_pages=max_pages)
    if tmp_dir is None:
        return ""
    try:
        parts: list[str] = []
        for index, image in enumerate(images, 1):
            text = ocr_image(image)
            if text:
                parts.append(f"[第{index}页]\n{text}")
        return "\n".join(parts)[:MAX_OCR_CHARS]
    finally:
        cleanup_dir(tmp_dir)
