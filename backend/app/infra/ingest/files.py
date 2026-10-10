"""文件文本抽取总入口：一份登记表（formats.py）+ 一组抽取器，按**内容**分派。

调用方只需要 `extract_file(path)`：它会先嗅探文件的真实格式（魔数 + 容器结构），
再按 kind 分派到对应抽取器，返回统一的 `ExtractedFile`。设计目标：

- **扩展名不可信**：`作业.ppt` 实际是 PPTX、教务导出的无扩展名文件、改成 `.txt` 的
  网页，都能被正确解析；扩展名与内容不一致时在 warning 里说明；
- **降级安全**：任何抽取器抛异常都转成「空文本 + 中文提示」，绝不把 500 抛给用户；
- **OCR 自动化**：图片直接走 OCR；扫描版 PDF（无文本层）自动栅格化后 OCR，
  仍然失败才提示人工补充 —— 用户不必知道「扫描件要另外处理」这件事。

新增格式只需在 `formats.py` 登记 + 在这里挂一个 handler，其余链路
（上传接口、格式目录、压缩包展开、agent 文件理解）自动继承。
"""
from __future__ import annotations

from functools import partial
from pathlib import Path
from typing import Callable

from app.infra.ingest import archive, ebook, office, textfmt
from app.infra.ingest.base import ExtractedFile, MAX_TEXT_CHARS, clean_text
from .formats import (
    FORMATS, GROUP_ARCHIVE,
    KIND_BZ2, KIND_CODE, KIND_DOC, KIND_DOCX, KIND_EPUB, KIND_GZIP, KIND_HTML,
    KIND_IMAGE, KIND_IPYNB, KIND_JSON, KIND_MEDIA, KIND_MOBI, KIND_ODF, KIND_PDF,
    KIND_PPT, KIND_PPTX, KIND_RTF, KIND_SUBTITLE, KIND_TABLE_TEXT, KIND_TAR,
    KIND_TEXT, KIND_UNKNOWN, KIND_XLS, KIND_XLSX, KIND_XML, KIND_XZ, KIND_YAML,
    KIND_ZIP, UNKNOWN_SPEC, detect_format,
)

#: 扫描版 PDF 自动 OCR 的触发阈值：文本层不足这么多字就认为「基本没抽到」
PDF_OCR_TEXT_MIN = 200

#: 抽取器注册表：kind → 处理函数（新增格式只需在这里挂一行）
KIND_HANDLERS: dict[str, Callable[[Path], ExtractedFile]] = {
    KIND_PDF: office.extract_pdf,
    KIND_DOCX: office.extract_docx,
    KIND_DOC: partial(office.extract_ole_text, label="旧版 Word 文档"),
    KIND_PPTX: office.extract_pptx,
    KIND_PPT: partial(office.extract_ole_text, label="旧版 PowerPoint 演示文稿"),
    KIND_XLSX: office.extract_xlsx,
    KIND_XLS: office.extract_xls,
    KIND_EPUB: ebook.extract_epub,
    KIND_MOBI: ebook.extract_mobi,
    KIND_IPYNB: textfmt.extract_ipynb_text,
    KIND_HTML: textfmt.extract_html_text,
    KIND_RTF: textfmt.extract_rtf_text,
    KIND_XML: textfmt.extract_xml_text,
    KIND_JSON: textfmt.extract_json_text,
    KIND_YAML: textfmt.extract_yaml_text,
    KIND_TABLE_TEXT: textfmt.extract_table_text,
    KIND_SUBTITLE: textfmt.extract_subtitle_text,
    KIND_CODE: textfmt.extract_code_text,
    KIND_TEXT: textfmt.extract_plain_text,
    # 压缩包家族：一个统一入口按真实容器格式分派（zip / tar.gz / gz / bz2 / xz）
    KIND_ZIP: archive.extract_archive,
    KIND_TAR: archive.extract_archive,
    KIND_GZIP: archive.extract_archive,
    KIND_BZ2: archive.extract_archive,
    KIND_XZ: archive.extract_archive,
}

#: 扩展名 → 处理函数（兼容旧调用方与测试；图片/ODF 在运行时按具体格式再分派）
EXT_HANDLERS: dict[str, Callable[[Path], ExtractedFile]] = {
    spec.ext: KIND_HANDLERS[spec.kind]
    for spec in FORMATS.values()
    if spec.kind in KIND_HANDLERS
}
EXT_HANDLERS.update({
    ".odt": partial(office.extract_odf, kind="odt"),
    ".ods": partial(office.extract_odf, kind="ods"),
    ".odp": partial(office.extract_odf, kind="odp"),
    ".ppt": partial(office.extract_ole_text, label="旧版 PowerPoint 演示文稿"),
    ".doc": partial(office.extract_ole_text, label="旧版 Word 文档"),
})


def _spec_map():
    """扩展名 → FormatSpec（延迟取，避免与 formats 的导入顺序耦合）。"""
    from app.infra.ingest.formats import FORMATS

    return FORMATS


def extract_file(
    path: Path,
    *,
    allow_ocr: bool = True,
    detected=None,
    max_chars: int = MAX_TEXT_CHARS,
) -> ExtractedFile:
    """按内容抽取文件文本（统一入口，永不抛异常，除非图片尺寸校验拦截）。

    `detected` 可传入已算好的 `formats.Detected`（调用方通常已嗅探过一次）；
    `allow_ocr=False` 用于批处理/测试等不希望加载 OCR 模型的场景。
    """
    info = detected or detect_format(path.name, path=path)
    if not info.supported:
        return _unsupported(info)
    if info.kind == KIND_IMAGE:
        return _extract_image(path, info, allow_ocr=allow_ocr)
    handler = KIND_HANDLERS.get(info.kind)
    if handler is None and info.kind == KIND_ODF:
        handler = partial(office.extract_odf, kind=info.spec.ext.lstrip("."))
    if handler is None:
        return _unsupported(info)
    try:
        result = handler(path)
    except Exception:
        # 抽取器内部已尽量兜底；这里再兜一层，保证「坏文件不影响其它功能」
        if info.group == GROUP_ARCHIVE:
            # 压缩包坏掉是常见情况（下载中断、分卷缺失），说的要具体
            return ExtractedFile("", warnings=[
                f"{info.spec.label}无法读取，可能已损坏或不是标准压缩包；"
                "可重新下载，或先在本机解压后上传包内文档"
            ])
        return ExtractedFile("", warnings=[f"{info.spec.label}解析失败，文件可能已损坏；可另存为 PDF 后重试"])
    if not isinstance(result, ExtractedFile):  # pragma: no cover - 防御性
        return ExtractedFile("")
    result.text = clean_text(result.text, max_chars)
    if info.mismatch and info.declared_ext:
        result.with_warning(
            f"文件扩展名（{info.declared_ext}）与实际内容（{info.spec.label}）不一致，已按实际格式解析"
        )
    if info.kind == KIND_PDF:
        result = _maybe_ocr_pdf(path, result, allow_ocr=allow_ocr)
    if not result.text and not result.needs_ocr and not result.warnings:
        result.with_warning(f"{info.spec.label}里没有抽取到文字，请补充标题/标签或手动录入")
    return result


def _unsupported(info) -> ExtractedFile:
    """识别得出但本机做不了（音视频）/ 完全认不出：给可操作的引导。"""
    if info.kind == KIND_MEDIA:
        return ExtractedFile("", warnings=[
            f"{info.spec.label}暂不支持离线转写（本机不联网、也不内置语音识别）。"
            "可以上传配套的课件/讲义，或把录音转成文字后再上传。"
        ])
    note = info.spec.note or UNKNOWN_SPEC.note
    label = info.spec.label
    if info.kind == KIND_UNKNOWN:
        return ExtractedFile("", warnings=[f"未能识别这个文件的类型：{note}"])
    return ExtractedFile("", warnings=[f"{label}本机暂不支持文本抽取：{note}"])


def _extract_image(path: Path, info, *, allow_ocr: bool) -> ExtractedFile:
    """图片：先做像素校验（防解压炸弹），再走离线 OCR。"""
    from app.infra.ingest import ocr
    from app.infra.ingest.imagecheck import check_image_size

    check_image_size(path, ext=info.spec.ext)   # 413/422 由上传接口直接回给用户
    if not allow_ocr:
        return ExtractedFile("", needs_ocr=True)
    if not ocr.ocr_available():
        return ExtractedFile("", needs_ocr=True, warnings=[
            "图片文字识别需要 OCR 引擎（rapidocr-onnxruntime），当前环境不可用；"
            "请补充标题/标签，或把文字手动录入后再入库"
        ])
    text = ocr.ocr_image(path)
    if not text:
        return ExtractedFile("", needs_ocr=True, warnings=[
            "这张图片里没有识别出文字，请换一张更清晰的截图，或手动录入内容"
        ])
    return ExtractedFile(text, warnings=["文字来自离线 OCR 识别，可能有识别误差，可在确认前核对原文"])


def _maybe_ocr_pdf(path: Path, result: ExtractedFile, *, allow_ocr: bool) -> ExtractedFile:
    """扫描版 PDF：文本层不足时栅格化 + OCR（页数有上限，临时文件必定清理）。"""
    if not result.needs_ocr or not allow_ocr or len(result.text) >= PDF_OCR_TEXT_MIN:
        return result
    from app.infra.ingest import ocr

    if not ocr.ocr_available():
        result.with_warning(
            "这份 PDF 没有文本层（扫描件），且 OCR 引擎不可用；"
            "请改用文字版 PDF，或手动补充内容"
        )
        return result
    ocr_text = ocr.ocr_pdf(path)
    if not ocr_text:
        result.with_warning("扫描版 PDF 的 OCR 没有识别出文字，请换更清晰的版本或手动补充")
        return result
    merged = clean_text((result.text + "\n" + ocr_text) if result.text.strip() else ocr_text)
    merged_result = ExtractedFile(merged, needs_ocr=False, pages=result.pages)
    for warning in result.warnings:
        merged_result.with_warning(warning)
    merged_result.with_warning(
        f"扫描版 PDF：正文来自前 {ocr.PDF_OCR_MAX_PAGES} 页的离线 OCR 识别，可能有识别误差"
    )
    return merged_result


def supported_extensions() -> list[str]:
    """本机可抽取文本的扩展名清单（目录接口与提示文案用）。"""
    return sorted(ext for ext, spec in _spec_map().items() if spec.supported)


def _spec_map():
    from app.infra.ingest.formats import FORMATS

    return FORMATS


__all__ = [
    "EXT_HANDLERS", "KIND_HANDLERS", "PDF_OCR_TEXT_MIN", "ExtractedFile",
    "extract_file", "supported_extensions",
]
