"""上传文件格式登记表 + 内容嗅探：常见学习文件类型「全覆盖」的单一事实来源。

本模块只做两件事，都不产生副作用：

1. **登记表**（`FORMATS`）：把系统认识的所有扩展名登记成 `FormatSpec`
   （分组 / 中文名 / 抽取器 kind / 是否走 OCR / 备注），目录接口与前端
   `accept`、格式徽标、上传提示全部由这张表派生，避免散落在各处的
   「支持格式」字符串互相漂移。
2. **内容嗅探**（`sniff` / `detect_format`）：按**文件魔数**与**容器内部结构**
   判断真实格式，不信任扩展名。改名的课件（.ppt 实际是 .pptx）、教务系统导出的
   无扩展名课表、伪装成 .txt 的 HTML 都能被纠正过来；扩展名与内容不一致时
   在 `Detected.mismatch` 上打标记，供上传接口给出提示。

设计约束：
- 只用标准库 + 既有直接依赖（pymupdf/openpyxl/xlrd/python-docx/python-pptx/Pillow），
  不为「认识一个扩展名」引入新依赖；
- 任何分支都不抛异常：识别不出就返回 `None` / `other`，由调用方走「不支持」提示；
- 音视频等无法离线转写的类型也登记在表内（`supported=False`），
  这样用户得到的是「暂不支持音视频转写，请上传课件」而不是「无法识别的文件」。
"""
from __future__ import annotations

import io
import json as _json
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

# ---------------------------------------------------------------------------
# 分组
# ---------------------------------------------------------------------------

GROUP_DOCUMENT = "document"
GROUP_SLIDE = "slide"
GROUP_SHEET = "sheet"
GROUP_EBOOK = "ebook"
GROUP_NOTEBOOK = "notebook"
GROUP_MARKUP = "markup"
GROUP_DATA = "data"
GROUP_CODE = "code"
GROUP_SUBTITLE = "subtitle"
GROUP_IMAGE = "image"
GROUP_ARCHIVE = "archive"
GROUP_MEDIA = "media"
GROUP_OTHER = "other"

#: 目录接口与前端分区展示顺序（也是 accept 串的排序依据）
GROUP_ORDER: tuple[str, ...] = (
    GROUP_DOCUMENT, GROUP_SLIDE, GROUP_SHEET, GROUP_EBOOK, GROUP_NOTEBOOK,
    GROUP_MARKUP, GROUP_DATA, GROUP_CODE, GROUP_SUBTITLE, GROUP_IMAGE,
    GROUP_ARCHIVE, GROUP_MEDIA,
)

GROUP_LABELS: dict[str, str] = {
    GROUP_DOCUMENT: "文档",
    GROUP_SLIDE: "演示文稿",
    GROUP_SHEET: "表格",
    GROUP_EBOOK: "电子书",
    GROUP_NOTEBOOK: "代码笔记本",
    GROUP_MARKUP: "网页与标记",
    GROUP_DATA: "结构化数据",
    GROUP_CODE: "代码与脚本",
    GROUP_SUBTITLE: "字幕与听记",
    GROUP_IMAGE: "图片（OCR 识别）",
    GROUP_ARCHIVE: "压缩包",
    GROUP_MEDIA: "音视频",
    GROUP_OTHER: "其他",
}

# ---------------------------------------------------------------------------
# 抽取器 kind（files.py 按 kind 分派，不与扩展名耦合）
# ---------------------------------------------------------------------------

KIND_PDF = "pdf"
KIND_DOCX = "docx"
KIND_DOC = "doc"
KIND_PPTX = "pptx"
KIND_PPT = "ppt"
KIND_XLSX = "xlsx"
KIND_XLS = "xls"
KIND_ODF = "odf"          # odt / ods / odp
KIND_EPUB = "epub"
KIND_MOBI = "mobi"
KIND_IPYNB = "ipynb"
KIND_HTML = "html"
KIND_RTF = "rtf"
KIND_XML = "xml"
KIND_JSON = "json"
KIND_YAML = "yaml"
KIND_TABLE_TEXT = "table_text"   # csv / tsv：按表格渲染成对齐文本
KIND_SUBTITLE = "subtitle"
KIND_CODE = "code"
KIND_TEXT = "text"
KIND_IMAGE = "image"             # 图片：交给 OCR 通道
# 压缩包：按格式分 kind（各自用不同的容器解析器，缺解码器的登记 supported=False）
KIND_ZIP = "zip"                 # ZIP 家族（含 docx/xlsx 这类 ZIP 容器，嗅探会细分）
KIND_TAR = "tar"                 # tar 归档（含 .tar.gz/.tar.bz2/.tar.xz，流式读取）
KIND_GZIP = "gzip"               # 单文件 gzip 流（.gz/.tgz）
KIND_BZ2 = "bzip2"               # 单文件 bzip2 流（.bz2）
KIND_XZ = "xz"                   # 单文件 xz/lzma 流（.xz）
KIND_7Z = "7z"                   # 7-Zip：本机无标准库解码器，给可操作指引
KIND_RAR = "rar"                 # RAR：同上
KIND_DMG = "dmg"                 # macOS 磁盘映像：同上
KIND_ZSTD = "zstd"               # Zstandard：同上
KIND_MEDIA = "media"             # 认得出来，但离线无法转写
KIND_UNKNOWN = "unknown"


@dataclass(frozen=True)
class FormatSpec:
    """一种被登记的格式。"""

    ext: str                       # 规范扩展名（小写，带点）
    group: str                     # 所属分组
    label: str                     # 中文名（前端展示）
    kind: str                      # 抽取器 kind
    ocr: bool = False              # 是否需要 OCR 通道
    supported: bool = True         # 是否支持文本抽取（False = 只识别、给指引）
    note: str = ""                 # 展示给用户的补充说明

    @property
    def group_label(self) -> str:
        return GROUP_LABELS.get(self.group, self.group)


def _specs() -> list[FormatSpec]:
    """登记表正文：一张列表就是「支持格式」的全部事实。"""
    out: list[FormatSpec] = []

    # ---- 文档 ----
    out += [
        FormatSpec(".pdf", GROUP_DOCUMENT, "PDF 文档", KIND_PDF,
                   note="扫描版自动转 OCR"),
        FormatSpec(".docx", GROUP_DOCUMENT, "Word 文档", KIND_DOCX,
                   note="正文 + 表格 + 页眉页脚"),
        FormatSpec(".doc", GROUP_DOCUMENT, "Word 97-2003 文档", KIND_DOC,
                   note="旧版二进制格式，尽力抽取，建议另存为 .docx"),
        FormatSpec(".odt", GROUP_DOCUMENT, "OpenDocument 文本文档", KIND_ODF),
        FormatSpec(".rtf", GROUP_DOCUMENT, "RTF 富文本", KIND_RTF),
        FormatSpec(".txt", GROUP_DOCUMENT, "纯文本", KIND_TEXT),
        FormatSpec(".md", GROUP_DOCUMENT, "Markdown", KIND_TEXT),
        FormatSpec(".markdown", GROUP_DOCUMENT, "Markdown", KIND_TEXT),
        FormatSpec(".tex", GROUP_DOCUMENT, "LaTeX 源文件", KIND_TEXT),
        FormatSpec(".log", GROUP_DOCUMENT, "日志文本", KIND_TEXT),
    ]
    # ---- 演示 ----
    out += [
        FormatSpec(".pptx", GROUP_SLIDE, "PowerPoint 演示文稿", KIND_PPTX,
                   note="含表格、图表与演讲者备注"),
        FormatSpec(".ppt", GROUP_SLIDE, "PowerPoint 97-2003 演示文稿", KIND_PPT,
                   note="旧版二进制格式，尽力抽取，建议另存为 .pptx"),
        FormatSpec(".odp", GROUP_SLIDE, "OpenDocument 演示文稿", KIND_ODF),
    ]
    # ---- 表格 ----
    out += [
        FormatSpec(".xlsx", GROUP_SHEET, "Excel 工作簿", KIND_XLSX,
                   note="多工作表逐个渲染成表格文本"),
        FormatSpec(".xlsm", GROUP_SHEET, "Excel 启用宏的工作簿", KIND_XLSX),
        FormatSpec(".xls", GROUP_SHEET, "Excel 97-2003 工作簿", KIND_XLS,
                   note="建议另存为 .xlsx"),
        FormatSpec(".ods", GROUP_SHEET, "OpenDocument 电子表格", KIND_ODF),
        FormatSpec(".csv", GROUP_SHEET, "CSV 表格", KIND_TABLE_TEXT),
        FormatSpec(".tsv", GROUP_SHEET, "TSV 表格", KIND_TABLE_TEXT),
    ]
    # ---- 电子书 ----
    out += [
        FormatSpec(".epub", GROUP_EBOOK, "EPUB 电子书", KIND_EPUB,
                   note="按书籍目录顺序抽取正文"),
        FormatSpec(".mobi", GROUP_EBOOK, "Mobipocket 电子书", KIND_MOBI,
                   note="旧版 Kindle 格式，尽力抽取"),
        FormatSpec(".azw3", GROUP_EBOOK, "Kindle AZW3 电子书", KIND_MOBI,
                   note="旧版 Kindle 格式，尽力抽取"),
    ]
    # ---- 代码笔记本 ----
    out += [
        FormatSpec(".ipynb", GROUP_NOTEBOOK, "Jupyter Notebook", KIND_IPYNB,
                   note="Markdown 单元格 + 代码 + 输出"),
    ]
    # ---- 网页与标记 ----
    out += [
        FormatSpec(".html", GROUP_MARKUP, "网页 HTML", KIND_HTML),
        FormatSpec(".htm", GROUP_MARKUP, "网页 HTML", KIND_HTML),
        FormatSpec(".xhtml", GROUP_MARKUP, "XHTML 网页", KIND_HTML),
        FormatSpec(".xml", GROUP_MARKUP, "XML 文档", KIND_XML),
    ]
    # ---- 结构化数据 ----
    out += [
        FormatSpec(".json", GROUP_DATA, "JSON 数据", KIND_JSON),
        FormatSpec(".yaml", GROUP_DATA, "YAML 配置", KIND_YAML),
        FormatSpec(".yml", GROUP_DATA, "YAML 配置", KIND_YAML),
        FormatSpec(".toml", GROUP_DATA, "TOML 配置", KIND_TEXT),
        FormatSpec(".ini", GROUP_DATA, "INI 配置", KIND_TEXT),
        FormatSpec(".ics", GROUP_DATA, "iCalendar 日历", KIND_TEXT,
                   note="可在「我的课程」里按课表导入"),
    ]
    # ---- 代码与脚本 ----
    for ext, label in (
        (".py", "Python 代码"), (".js", "JavaScript 代码"), (".jsx", "React JSX"),
        (".ts", "TypeScript 代码"), (".tsx", "React TSX"), (".vue", "Vue 组件"),
        (".svelte", "Svelte 组件"), (".java", "Java 代码"), (".kt", "Kotlin 代码"),
        (".scala", "Scala 代码"), (".c", "C 代码"), (".h", "C 头文件"),
        (".cpp", "C++ 代码"), (".cc", "C++ 代码"), (".hpp", "C++ 头文件"),
        (".cs", "C# 代码"), (".go", "Go 代码"), (".rs", "Rust 代码"),
        (".rb", "Ruby 代码"), (".php", "PHP 代码"), (".swift", "Swift 代码"),
        (".dart", "Dart 代码"), (".m", "MATLAB / Objective-C 代码"),
        (".jl", "Julia 代码"), (".lua", "Lua 代码"), (".pl", "Perl 代码"),
        (".r", "R 代码"), (".sql", "SQL 脚本"), (".sh", "Shell 脚本"),
        (".bash", "Shell 脚本"), (".zsh", "Shell 脚本"), (".bat", "批处理脚本"),
        (".ps1", "PowerShell 脚本"), (".asm", "汇编代码"),
    ):
        out.append(FormatSpec(ext, GROUP_CODE, label, KIND_CODE))
    # ---- 字幕与听记 ----
    out += [
        FormatSpec(".srt", GROUP_SUBTITLE, "SRT 字幕", KIND_SUBTITLE),
        FormatSpec(".vtt", GROUP_SUBTITLE, "WebVTT 字幕", KIND_SUBTITLE),
        FormatSpec(".ass", GROUP_SUBTITLE, "ASS/SSA 字幕", KIND_SUBTITLE),
        FormatSpec(".ssa", GROUP_SUBTITLE, "ASS/SSA 字幕", KIND_SUBTITLE),
        FormatSpec(".lrc", GROUP_SUBTITLE, "LRC 歌词/听记", KIND_SUBTITLE),
    ]
    # ---- 图片（OCR）----
    for ext, label in (
        (".png", "PNG 图片"), (".jpg", "JPEG 图片"), (".jpeg", "JPEG 图片"),
        (".webp", "WebP 图片"), (".bmp", "BMP 图片"), (".gif", "GIF 图片"),
        (".tif", "TIFF 图片"), (".tiff", "TIFF 图片"), (".heic", "HEIC 图片"),
        (".heif", "HEIF 图片"), (".avif", "AVIF 图片"),
    ):
        note = "离线 OCR 识别文字，可核对识别原文"
        if ext in (".heic", ".heif"):
            note = "离线 OCR 识别；本机若缺 HEIC 解码器请先转成 JPG/PNG"
        out.append(FormatSpec(ext, GROUP_IMAGE, label, KIND_IMAGE, ocr=True, note=note))
    # ---- 压缩包 ----
    out += [
        FormatSpec(".zip", GROUP_ARCHIVE, "ZIP 压缩包", KIND_ZIP,
                   note="自动抽取包内可解析的文档/文本"),
        FormatSpec(".tar", GROUP_ARCHIVE, "TAR 归档", KIND_TAR,
                   note="自动抽取包内可解析的文档/文本"),
        FormatSpec(".tar.gz", GROUP_ARCHIVE, "TAR.GZ 压缩包", KIND_TAR,
                   note="流式解压后抽取包内文档（.tgz 同此格式）"),
        FormatSpec(".tgz", GROUP_ARCHIVE, "TAR.GZ 压缩包", KIND_TAR,
                   note="与 .tar.gz 相同，自动抽取包内文档"),
        FormatSpec(".tar.bz2", GROUP_ARCHIVE, "TAR.BZ2 压缩包", KIND_TAR,
                   note="流式解压后抽取包内文档（.tbz2 同此格式）"),
        FormatSpec(".tar.xz", GROUP_ARCHIVE, "TAR.XZ 压缩包", KIND_TAR,
                   note="流式解压后抽取包内文档（.txz 同此格式）"),
        FormatSpec(".gz", GROUP_ARCHIVE, "GZIP 压缩文件", KIND_GZIP,
                   note="解压后按真实内容抽取；若是 tar.gz 会自动展开"),
        FormatSpec(".bz2", GROUP_ARCHIVE, "BZIP2 压缩文件", KIND_BZ2,
                   note="解压后按真实内容抽取"),
        FormatSpec(".tbz2", GROUP_ARCHIVE, "TAR.BZ2 压缩包", KIND_TAR,
                   note="与 .tar.bz2 相同，自动抽取包内文档"),
        FormatSpec(".xz", GROUP_ARCHIVE, "XZ 压缩文件", KIND_XZ,
                   note="解压后按真实内容抽取"),
        FormatSpec(".txz", GROUP_ARCHIVE, "TAR.XZ 压缩包", KIND_TAR,
                   note="与 .tar.xz 相同，自动抽取包内文档"),
        # 下面几种「认得出来但本机解不开」：登记是为了给可操作指引，
        # 而不是让用户看到「无法识别的文件」（与 HEIC 缺解码器同一套降级口径）
        FormatSpec(".7z", GROUP_ARCHIVE, "7-Zip 压缩包", KIND_7Z, supported=False,
                   note="本机没有 7z 解码器：请先用 7-Zip 解压，把里面的课件/讲义单独上传"),
        FormatSpec(".rar", GROUP_ARCHIVE, "RAR 压缩包", KIND_RAR, supported=False,
                   note="本机没有 RAR 解码器：请先用 WinRAR / 7-Zip 解压后上传包内文档"),
        FormatSpec(".dmg", GROUP_ARCHIVE, "DMG 磁盘映像", KIND_DMG, supported=False,
                   note="磁盘映像需先挂载拷贝出资料，再把课件/讲义单独上传"),
        FormatSpec(".zst", GROUP_ARCHIVE, "Zstandard 压缩文件", KIND_ZSTD, supported=False,
                   note="请先用 zstd -d 解压（或用 7-Zip）后上传解压结果"),
    ]
    # ---- 音视频（认得出来，但离线无法转写）----
    for ext, label in (
        (".mp3", "MP3 音频"), (".wav", "WAV 音频"), (".m4a", "M4A 音频"),
        (".aac", "AAC 音频"), (".flac", "FLAC 音频"), (".ogg", "OGG 音频"),
        (".mp4", "MP4 视频"), (".mov", "MOV 视频"), (".mkv", "MKV 视频"),
        (".avi", "AVI 视频"), (".wmv", "WMV 视频"), (".webm", "WebM 视频"),
    ):
        out.append(FormatSpec(ext, GROUP_MEDIA, label, KIND_MEDIA, supported=False,
                              note="本地不联网转写，请上传课件/讲义或录音文稿"))
    return out


#: 规范扩展名 → FormatSpec
FORMATS: dict[str, FormatSpec] = {s.ext: s for s in _specs()}

#: 扩展名别名 → 规范扩展名（同一个抽取器的不同写法；别名不再单列进登记表）
ALIASES: dict[str, str] = {
    ".jpe": ".jpg", ".jfif": ".jpg", ".markdn": ".md", ".mdown": ".md",
    ".text": ".txt", ".textile": ".txt", ".wave": ".wav", ".oga": ".ogg",
}
#: 压缩包别名 → 规范扩展名（.tar.gz/.tar.bz2/.tar.xz 这类两段式写法）
ARCHIVE_ALIASES: dict[str, str] = {
    ".tbz": ".tar.bz2", ".tbz2": ".tar.bz2", ".tb2": ".tar.bz2",
    ".txz": ".tar.xz", ".tlz": ".tar.xz", ".taz": ".tar.gz",
}
ALIASES.update(ARCHIVE_ALIASES)

UNKNOWN_SPEC = FormatSpec(".bin", GROUP_OTHER, "未知类型", KIND_UNKNOWN,
                          supported=False, note="未识别的文件类型，可转成 PDF/TXT 后重试")

#: 图片扩展名集合（OCR 通道判定用，含别名归并后的结果）
IMAGE_EXTS: frozenset[str] = frozenset(
    s.ext for s in FORMATS.values() if s.kind == KIND_IMAGE
)
#: 需要 OCR 的扩展名（= 图片）
OCR_EXTS = IMAGE_EXTS


def normalize_ext(filename: str | Path | None) -> str:
    """文件名/路径/裸扩展名 → 规范小写扩展名（无扩展名返回 ""）。

    支持两段式扩展名（`.tar.gz` / `.tar.bz2` / `.tar.xz`）：先试「最后两段」，
    再退回「最后一段」，因此 `资料.tar.gz` 与 `资料.tgz` 都归到同一个规范扩展名，
    前端格式徽标与中文名才不会把 tar.gz 显示成「GZIP 压缩文件」。
    """
    if filename is None:
        return ""
    name = str(filename).strip().replace("\\", "/").rsplit("/", 1)[-1]
    if not name:
        return ""
    parts = name.split(".")
    if len(parts) < 2 or not parts[-1]:        # 无扩展名 / 以点结尾（"teacher."）
        return ""
    candidates: list[str] = []
    if len(parts) >= 3 and parts[-2]:
        candidates.append(f".{parts[-2]}.{parts[-1]}".lower())
    candidates.append(f".{parts[-1]}".lower())
    for ext in candidates:
        if len(ext) > 12 or not all(part.isalnum() for part in ext[1:].split(".")):
            continue
        if ext in FORMATS or ext in ALIASES:
            return ALIASES.get(ext, ext)
    return ""


def spec_for(ext_or_name: str | Path | None) -> FormatSpec | None:
    """扩展名/文件名 → FormatSpec（未登记返回 None）。"""
    ext = normalize_ext(ext_or_name)
    return FORMATS.get(ext) if ext else None


def accept_string() -> str:
    """前端 ``<input accept>`` 用的扩展名清单（按分组顺序，稳定可复现）。"""
    order = {g: i for i, g in enumerate(GROUP_ORDER)}
    specs = sorted(FORMATS.values(), key=lambda s: (order.get(s.group, 99), s.ext))
    return ",".join(s.ext for s in specs)


# ---------------------------------------------------------------------------
# AI 笔记提炼（识图 / 文档）可直传的格式
# ---------------------------------------------------------------------------

#: 提炼面板不接受的分组：音视频离线无法转写、未知类型抽不出正文
REFINE_EXCLUDED_GROUPS: tuple[str, ...] = (GROUP_MEDIA, GROUP_OTHER)


def refine_specs() -> list[FormatSpec]:
    """「AI 笔记提炼」可直传的格式：能抽出正文，且本机解码器可用。

    图片（OCR）、PDF/Word/Excel/PPT/ODF、电子书、文本与标记、字幕、压缩包都在内；
    唯一被排除的是音视频（本机不联网、无语音识别）与认不出的类型。
    """
    return [
        s for s in FORMATS.values()
        if s.supported and s.group not in REFINE_EXCLUDED_GROUPS and decoder_available(s.ext)
    ]


def refine_accept_string() -> str:
    """提炼面板 ``<input accept>`` 的扩展名清单（与上传接口同一张登记表派生）。"""
    order = {g: i for i, g in enumerate(GROUP_ORDER)}
    specs = sorted(refine_specs(), key=lambda s: (order.get(s.group, 99), s.ext))
    return ",".join(s.ext for s in specs)


def is_refinable(ext_or_name: str | Path | None) -> bool:
    """该扩展名/文件名是否可直接用于 AI 笔记提炼。"""
    spec = spec_for(ext_or_name)
    return bool(
        spec
        and spec.supported
        and spec.group not in REFINE_EXCLUDED_GROUPS
        and decoder_available(spec.ext)
    )


def decoder_available(ext_or_name: str | Path | None) -> bool:
    """本机是否具备该格式的解码能力（图片看 Pillow 注册表，其余看依赖是否装得上）。

    压缩包这一族用的是标准库解码器（zipfile/tarfile/gzip/bz2/lzma），因此只有
    登记时就标了 `supported=False` 的格式（7z/rar/dmg/zst）在本机不可用。
    """
    ext = normalize_ext(ext_or_name)
    if ext in IMAGE_EXTS:
        try:
            from PIL import Image

            registered = Image.registered_extensions()
        except Exception:  # pragma: no cover - Pillow 缺失时按不可用处理
            return False
        return ext in registered
    return True


def catalog(ocr_available: bool | None = None) -> dict:
    """格式目录（`GET /api/ingest/formats` 的载荷）：分组 → 格式清单。

    `supported` 是**本机实际可用性**：HEIC 这类登记了但本机缺解码器的格式
    会被降级标记（并保留 note 指引），避免前端承诺了做不到的事。
    """
    order = {g: i for i, g in enumerate(GROUP_ORDER)}
    groups: list[dict] = []
    for group in sorted({s.group for s in FORMATS.values()}, key=lambda g: order.get(g, 99)):
        items = sorted(
            (s for s in FORMATS.values() if s.group == group), key=lambda s: s.ext
        )
        groups.append({
            "id": group,
            "label": GROUP_LABELS.get(group, group),
            "formats": [
                {
                    "ext": s.ext,
                    "label": s.label,
                    "supported": s.supported and decoder_available(s.ext),
                    "ocr": s.ocr,
                    "note": s.note,
                    "group": s.group,
                }
                for s in items
            ],
        })
    return {
        "groups": groups,
        "accept": accept_string(),
        "refine_accept": refine_accept_string(),
        "extensions": sorted(FORMATS),
        # 别名 → 规范扩展名：`.tgz` 与 `.tar.gz` 是同一个格式，前端拿它做
        # 「拖进来的文件到底算哪个格式」的归并（否则 accept 里没有 .tgz 会误拦上传）
        "aliases": dict(sorted(ALIASES.items())),
        "unknown_note": UNKNOWN_SPEC.note,
        "ocr_available": bool(ocr_available),
    }


# ---------------------------------------------------------------------------
# 内容嗅探（魔数 + 容器内部结构）
# ---------------------------------------------------------------------------

_PDF_MAGIC = b"%PDF-"
_ZIP_MAGIC = b"PK\x03\x04"
_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_RTF_MAGIC = b"{\\rtf"
_GZIP_MAGIC = b"\x1f\x8b"
_BZIP2_MAGIC = b"BZh"
_XZ_MAGIC = b"\xfd7zXZ\x00"
_7Z_MAGIC = b"7z\xbc\xaf\x27\x1c"
_RAR_MAGIC = b"Rar!\x1a\x07"
_ZSTD_MAGIC = b"(\xb5/\xfd"
#: 压缩包家族的魔数 → (规范扩展名, 容器名)。只判到「压缩层」这一级：
#: 单文件流（gz/bz2/xz）里到底是 tar 还是某个文档，解压后由 sniff 再看一次。
_COMPRESS_MAGIC: tuple[tuple[bytes, str, str], ...] = (
    (_GZIP_MAGIC, ".gz", "gzip"),
    (_BZIP2_MAGIC, ".bz2", "bzip2"),
    (_XZ_MAGIC, ".xz", "xz"),
    (_7Z_MAGIC, ".7z", "7z"),
    (_RAR_MAGIC, ".rar", "rar"),
    (_ZSTD_MAGIC, ".zst", "zstd"),
)
#: tar 头部的 ustar 魔数（编号 257 起，含 GNU tar 的 "ustar  " 变体）
_TAR_OFFSET = 257
_TAR_MAGIC = b"ustar"
_IMAGE_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"\xff\xd8\xff", ".jpg"),
    (b"GIF87a", ".gif"),
    (b"GIF89a", ".gif"),
    (b"BM", ".bmp"),
    (b"II*\x00", ".tif"),
    (b"MM\x00*", ".tif"),
)
_AUDIO_VIDEO_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"ID3", ".mp3"),
    (b"\xff\xfb", ".mp3"),
    (b"\xff\xf3", ".mp3"),
    (b"fLaC", ".flac"),
    (b"OggS", ".ogg"),
    (b"RIFF", ".wav"),          # 需再看 WAVE/FORM 子类型
    (b"\x1aE\xdf\xa3", ".webm"),
)
#: OLE2 复合文档里的流名（UTF-16LE 字节扫描）→ 规范扩展名
_OLE_STREAMS: tuple[tuple[bytes, str], ...] = (
    (b"W\x00o\x00r\x00d\x00D\x00o\x00c\x00u\x00m\x00e\x00n\x00t", ".doc"),
    (b"W\x00o\x00r\x00k\x00b\x00o\x00o\x00k", ".xls"),
    (b"B\x00o\x00o\x00k\x00", ".xls"),
    (b"P\x00o\x00w\x00e\x00r\x00P\x00o\x00i\x00n\x00t\x00 \x00D\x00o\x00c\x00u\x00m\x00e\x00n\x00t", ".ppt"),
)
#: ZIP 内部标记 → 规范扩展名（按顺序匹配，先命中先返回）
_ZIP_MARKERS: tuple[tuple[str, str], ...] = (
    ("word/document.xml", ".docx"),
    ("xl/workbook.xml", ".xlsx"),
    ("ppt/presentation.xml", ".pptx"),
    ("META-INF/container.xml", ".epub"),
)


def _odf_ext_from_mime(mime: str) -> str:
    """OPF/ODF 容器的 mimetype → 规范扩展名（认不出返回空）。"""
    if "epub" in mime:
        return ".epub"
    if "spreadsheet" in mime:
        return ".ods"
    if "presentation" in mime:
        return ".odp"
    if "wordprocessing" in mime or "opendocument.text" in mime:
        return ".odt"
    if "opendocument" in mime:
        return ".odt"
    return ""

_HEAD_BYTES = 8192
_OLE_SCAN_BYTES = 262_144     # OLE 目录区一般在前 256KB 内
_CONTAINER_READ_LIMIT = 512 * 1024 * 1024


@dataclass
class Detected:
    """一次格式判定结果。"""

    spec: FormatSpec
    ext: str = ""                    # 嗅探出的规范扩展名（未知时为空）
    declared_ext: str = ""           # 文件名/请求声明的扩展名
    container: str = ""              # 容器家族：zip / tar / gzip / ole2 / ""（用于诊断）
    mismatch: bool = False           # 扩展名与内容不一致
    sniffed: bool = False            # 是否由内容（而非扩展名）判定

    @property
    def kind(self) -> str:
        return self.spec.kind

    @property
    def group(self) -> str:
        return self.spec.group

    @property
    def supported(self) -> bool:
        return self.spec.supported

    def as_dict(self) -> dict:
        """接口回包用的精简结构（不泄露本地路径等实现细节）。"""
        return {
            "ext": self.spec.ext,
            "label": self.spec.label,
            "group": self.spec.group,
            "group_label": self.spec.group_label,
            "kind": self.spec.kind,
            "ocr": self.spec.ocr,
            "supported": self.spec.supported,
            "note": self.spec.note,
            "detected_ext": self.ext,
            "declared_ext": self.declared_ext,
            "container": self.container,
            "mismatch": self.mismatch,
            "sniffed": self.sniffed,
        }


def _head(source: bytes | Path) -> bytes:
    """取前 8KB 用于魔数判断（字节/路径都支持，路径只读头部不整文件载入）。"""
    if isinstance(source, (bytes, bytearray)):
        return bytes(source[:_HEAD_BYTES])
    try:
        with open(source, "rb") as f:
            return f.read(_HEAD_BYTES)
    except OSError:
        return b""


def _read_more(source: bytes | Path, size: int) -> bytes:
    if isinstance(source, (bytes, bytearray)):
        return bytes(source[:size])
    try:
        with open(source, "rb") as f:
            return f.read(size)
    except OSError:
        return b""


def _zip_probe(source: bytes | Path) -> tuple[list[str], str]:
    """一次打开 ZIP，取条目名与根部 mimetype 内容（失败返回 ([], "")，绝不抛）。"""
    try:
        if isinstance(source, (bytes, bytearray)):
            if len(source) > _CONTAINER_READ_LIMIT:
                return [], ""
            handle: object = io.BytesIO(bytes(source))
        else:
            handle = source
        with zipfile.ZipFile(handle) as zf:
            names = zf.namelist()[:2000]
            mime = ""
            if "mimetype" in names:
                try:
                    mime = zf.read("mimetype")[:200].decode("ascii", errors="ignore")
                except (KeyError, OSError, ValueError, EOFError):
                    mime = ""
            return names, mime
    except (zipfile.BadZipFile, OSError, ValueError, EOFError, NotImplementedError):
        return [], ""


def _zip_names(source: bytes | Path) -> list[str]:
    """ZIP 中央目录里的条目名（失败返回 []）。"""
    return _zip_probe(source)[0]


def _zip_mimetype(source: bytes | Path) -> str:
    """OPF/ODF 容器根部的 ``mimetype`` 条目内容（ODF 用它区分 odt/ods/odp）。"""
    return _zip_probe(source)[1]


def _sniff_zip(source: bytes | Path) -> tuple[str, str]:
    """ZIP 容器 → (规范扩展名, 容器名)。识别不出具体子类型时返回 ('.zip', 'zip')。"""
    names = _zip_names(source)
    if not names:
        return ".zip", "zip"
    joined = "\n".join(names)
    # ODF/EPUB 都靠根部 mimetype 条目声明自己是哪种容器，先看它（比路径标记更权威）
    mime_ext = _odf_ext_from_mime(_zip_mimetype(source))
    if mime_ext:
        return mime_ext, "zip"
    for marker, ext in _ZIP_MARKERS:
        if marker in joined:
            return ext, "zip"
    # 没有 mimetype 的 ODF（少数导出工具会漏）：看内容目录
    if any(n.startswith("Object ") or n == "content.xml" for n in names):
        return ".odt", "zip"
    return ".zip", "zip"


def _sniff_ole(source: bytes | Path) -> str:
    """OLE2 复合文档 → 扩展名：扫目录区里的流名（doc/xls/ppt）。"""
    blob = _read_more(source, _OLE_SCAN_BYTES)
    for needle, ext in _OLE_STREAMS:
        if needle in blob:
            return ext
    return ".doc"


def is_tar(source: bytes | Path) -> bool:
    """是不是 tar 归档（看编号 257 起的 ustar 魔数，并校验首块校验和）。

    校验和这一层是必要的：普通文件里偏移 257 处恰好出现 ``ustar`` 的概率虽低，
    但一旦误判就会把正常文件当压缩包解析，用户看到「压缩包损坏」。
    校验和字段按 POSIX 的算法「当作 8 个空格」参与计算（GNU tar 的
    有符号求和结果在 ASCII 头里与之一致，这里一并接受）。
    """
    head = _head(source)
    if len(head) < 512 or head[_TAR_OFFSET:_TAR_OFFSET + len(_TAR_MAGIC)] != _TAR_MAGIC:
        return False
    blank = head[:148] + b" " * 8 + head[156:512]
    try:                        # 校验和字段是 6 位八进制 + NUL/空格填充
        stored = int(head[148:156].strip(b"\x00 ").decode("ascii") or "0", 8)
    except (UnicodeDecodeError, ValueError):
        return False
    candidates = {
        sum(blank),
        sum(b if b < 128 else b - 256 for b in blank),    # 有符号字节的历史实现
        sum(head[:148]) + sum(head[156:512]),             # 少数实现把该字段按 0 计
    }
    return stored in candidates


def _sniff_compressed(source: bytes | Path, magic: bytes) -> str:
    """单文件压缩流（gz/bz2/xz）→ 规范扩展名：解压头部看里面是不是 tar。

    ``作业.tar.gz`` 与 ``讲义.txt.gz`` 的外层魔数是一样的，只有解压后才知道
    该按「压缩包」展开还是按「单文档」抽取，所以这里真的解一小段。
    """
    from .archive import open_stream_reader

    prefix = _COMPRESS_MAGIC_EXT.get(magic, ".gz")
    try:
        with open_stream_reader(source, magic) as reader:
            probe = reader.read(512)
    except Exception:           # 缺解码器/数据损坏：退化成「只认得扩展名」
        return prefix
    if len(probe) >= 512 and probe[_TAR_OFFSET:_TAR_OFFSET + len(_TAR_MAGIC)] == _TAR_MAGIC:
        return _TAR_BY_MAGIC.get(prefix, ".tar.gz")
    return prefix


#: 压缩魔数 → 里面是 tar 时的规范扩展名
_TAR_BY_MAGIC: dict[str, str] = {
    ".gz": ".tar.gz", ".bz2": ".tar.bz2", ".xz": ".tar.xz",
}
#: 压缩魔数 → 单纯压缩流时的规范扩展名
_COMPRESS_MAGIC_EXT: dict[bytes, str] = {
    magic: ext for magic, ext, _ in _COMPRESS_MAGIC
}


def sniff(source: bytes | Path) -> str:
    """内容 → 规范扩展名（识别不出返回 ""）。不信任文件名。"""
    head = _head(source)
    if not head:
        return ""
    if head.startswith(_PDF_MAGIC):
        return ".pdf"
    if head.startswith(_ZIP_MAGIC):
        return _sniff_zip(source)[0]
    if head.startswith(_OLE_MAGIC):
        return _sniff_ole(source)
    if head.startswith(_RTF_MAGIC):
        return ".rtf"
    for magic, ext, _container in _COMPRESS_MAGIC:
        if head.startswith(magic):
            if magic in (_GZIP_MAGIC, _BZIP2_MAGIC, _XZ_MAGIC):
                return _sniff_compressed(source, magic)
            return ext
    # tar 放在图片之前：tar 的 ustar 魔数在偏移 257，不会被普通图片误命中，
    # 但「首字符恰好是 BM」的文件要先排除掉（旧 tar 允许任意文件名开头）
    if is_tar(source):
        return ".tar"
    for magic, ext in _IMAGE_MAGIC:
        if head.startswith(magic):
            return ext
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp"
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in (b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1"):
            return ".heic"
        if brand in (b"avif", b"avis"):
            return ".avif"
        if brand in (b"mp42", b"isom", b"M4V ", b"M4A ", b"qt  "):
            return ".m4a" if brand == b"M4A " else ".mp4"
    for magic, ext in _AUDIO_VIDEO_MAGIC:
        if head.startswith(magic):
            if magic == b"RIFF" and head[8:12] == b"WAVE":
                return ".wav"
            if magic == b"RIFF":
                return ".avi" if head[8:12] == b"AVI " else ""
            return ext
    # 文本类：只在前 4KB 没有 NUL 时判断（二进制混入 NUL 的不当文本）
    sample = _read_more(source, 4096)
    if b"\x00" in sample[:2048]:
        return ""
    text = _decode_text(sample)
    if text is None:
        return ""
    stripped = text.lstrip("\ufeff \t\r\n")
    low = stripped[:400].lower()
    if low.startswith("<!doctype html") or low.startswith("<html") or "<html" in low[:200]:
        return ".html"
    if stripped.startswith("<?xml"):
        return ".xml"
    if stripped.upper().startswith("WEBVTT"):
        return ".vtt"
    if stripped.upper().startswith("BEGIN:VCALENDAR"):
        return ".ics"
    if stripped[:1] in ("{", "["):
        try:
            _json.loads(stripped[:200_000])
        except (ValueError, TypeError):
            pass
        else:
            return ".json"
    return ""


def _decode_text(data: bytes) -> str | None:
    """UTF-8 / GB18030 能解出即当文本（与 decode_upload_text 口径一致的最小实现）。"""
    for enc in ("utf-8-sig", "utf-8", "gb18030", "big5"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return None


#: 「通用文本容器」类 kind：内容嗅探只能判到这一层，专门格式要靠扩展名细分
GENERIC_KINDS: frozenset[str] = frozenset({KIND_TEXT, KIND_CODE, KIND_JSON, KIND_XML})


def detect_format(
    filename: str | Path = "",
    *,
    data: bytes | None = None,
    path: Path | None = None,
) -> Detected:
    """判定一个上传文件的真实格式（文件名 + 内容，内容优先）。

    内容能认出类型时以内容为准；内容认不出但扩展名登记过则用扩展名；
    两者都没有则返回 `UNKNOWN_SPEC`。

    唯一的例外是「通用容器」：`.ipynb`/`.srt` 的内容可能被嗅探成 JSON/纯文本，
    这时扩展名指向的解析器更专门，听扩展名的（否则 Notebook 会被当成普通 JSON）。
    """
    declared = normalize_ext(filename)
    source: bytes | Path | None = path if path is not None else data
    ext, container = "", ""
    if source is not None:
        try:
            ext = sniff(source)
        except Exception:  # pragma: no cover - 嗅探兜底，绝不因识别失败中断上传
            ext = ""
        if ext:
            head = _head(source if source is not None else b"")
            container = container_of_magic(head)
    if ext and ext in FORMATS:
        spec = FORMATS[ext]
        declared_spec = FORMATS.get(declared)
        if (declared_spec is not None and declared != ext
                and spec.kind in GENERIC_KINDS and declared_spec.kind not in GENERIC_KINDS):
            return Detected(declared_spec, ext=ext, declared_ext=declared,
                            container=container, sniffed=True)
        # 纯文本扩展名不算「不一致」：.txt 里放 JSON/HTML 是常见且合理的用法
        # 压缩包之间也不算：.gz 里是 tar.gz、.zip 里是 docx 都是正常写法，
        # 用户不需要被提醒「你写错了扩展名」；只有声明了 .7z/.rar 这类
        # 本机解不开的格式、内容却另有其时，才提示真实格式。
        mismatch = bool(
            declared_spec is not None
            and declared != ext
            and declared_spec.kind not in (KIND_TEXT, KIND_CODE)
            and declared_spec.kind != spec.kind
            and not (spec.group == GROUP_ARCHIVE and declared_spec.group == GROUP_ARCHIVE
                     and declared_spec.supported)
        )
        return Detected(spec, ext=ext, declared_ext=declared, container=container,
                        mismatch=mismatch, sniffed=True)
    if declared and declared in FORMATS:
        return Detected(FORMATS[declared], ext="", declared_ext=declared,
                        container=container, sniffed=False)
    return Detected(UNKNOWN_SPEC, ext=ext, declared_ext=declared, container=container)


def container_of_magic(head: bytes) -> str:
    """文件头 → 容器家族名（zip / tar / gzip / bzip2 / xz / 7z / rar / zstd / ole2）。

    只用于诊断展示（回包里的 `container` 字段），不影响分派。
    """
    if head.startswith(_ZIP_MAGIC):
        return "zip"
    if head.startswith(_OLE_MAGIC):
        return "ole2"
    for magic, _ext, container in _COMPRESS_MAGIC:
        if head.startswith(magic):
            return container
    if is_tar(head):
        return "tar"
    return ""


def describe(ext_or_name: str | Path | None) -> dict:
    """扩展名 → 展示用字典（未知类型返回 unknown 结构，供前端兜底）。"""
    spec = spec_for(ext_or_name)
    if spec is None:
        return {
            "ext": normalize_ext(ext_or_name), "label": UNKNOWN_SPEC.label,
            "group": UNKNOWN_SPEC.group, "group_label": UNKNOWN_SPEC.group_label,
            "kind": KIND_UNKNOWN, "ocr": False, "supported": False,
            "note": UNKNOWN_SPEC.note,
        }
    return {
        "ext": spec.ext, "label": spec.label, "group": spec.group,
        "group_label": spec.group_label, "kind": spec.kind, "ocr": spec.ocr,
        "supported": spec.supported, "note": spec.note,
    }


def is_image_ext(ext_or_name: str | Path | None) -> bool:
    return normalize_ext(ext_or_name) in IMAGE_EXTS


def group_of(ext_or_name: str | Path | None) -> str:
    spec = spec_for(ext_or_name)
    return spec.group if spec else GROUP_OTHER


def kinds_for(exts: Iterable[str]) -> list[str]:
    """扩展名集合 → 去重后的 kind 列表（测试与统计用）。"""
    seen: list[str] = []
    for e in exts:
        spec = spec_for(e)
        if spec and spec.kind not in seen:
            seen.append(spec.kind)
    return seen


__all__ = [
    "ALIASES", "ARCHIVE_ALIASES", "FORMATS", "GROUP_LABELS", "GROUP_ORDER",
    "IMAGE_EXTS", "OCR_EXTS", "REFINE_EXCLUDED_GROUPS", "UNKNOWN_SPEC",
    "Detected", "FormatSpec", "accept_string", "catalog", "container_of_magic",
    "decoder_available", "describe", "detect_format", "group_of", "is_image_ext",
    "is_refinable", "is_tar", "kinds_for", "normalize_ext", "refine_accept_string",
    "refine_specs", "sniff", "spec_for",
]
