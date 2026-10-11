"""压缩包抽取：把包内「能解析的文件」展开成一份合并文本。

真实场景里老师发的资料包既有 Windows 的 zip/rar/7z，也有 Linux/macOS 课的
tar.gz，直接丢弃等于让用户手工解压再逐个上传。这里做**有界展开**：

- **多格式**：zip（zipfile）、tar 及 tar.gz/tar.bz2/tar.xz（tarfile）、
  单文件 gz/bz2/xz（gzip/bz2/lzma）——全部走标准库，不引入新依赖；
  7z/rar/dmg/zst 本机没有解码器，登记表里标 `supported=False` 并给可操作指引，
  用户拿到的是「先解压再上传」而不是「无法识别的文件」。
- **只展开一层**：包内再套压缩包不展开（防 zip 套 zip 指数放大）；
- **三重上限**：条目数 × 单条体积 × 解压总量，声明体积与实际读取都校验（防压缩炸弹），
  解压一律**流式读**（tar.gz 不会整包进内存）；
- **文本类条目直接解码**；二进制条目（pdf/docx/pptx/xlsx/…）落临时文件后交给
  `files.extract_file`，因此**新增格式会自动被压缩包支持**，不必在这里维护第二份表；
- 单文件压缩流（`讲义.txt.gz`、`报告.pdf.gz`）解压后按**真实内容**分派，
  包一层压缩不影响抽取结果。
"""
from __future__ import annotations

import bz2
import gzip
import io
import lzma
import re
import tarfile
import tempfile
import zipfile
import zlib
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Callable, Iterator

from .base import ExtractedFile, clean_text, join_blocks, text_slice
from .formats import (
    GROUP_ARCHIVE, GROUP_DOCUMENT, GROUP_EBOOK, GROUP_IMAGE, GROUP_MEDIA,
    GROUP_SHEET, GROUP_SLIDE, KIND_BZ2, KIND_GZIP, KIND_TAR, KIND_UNKNOWN,
    KIND_XZ, KIND_ZIP, KIND_7Z, KIND_DMG, KIND_RAR, KIND_ZSTD,
)
from .textfmt import decode_text

MAX_TEXT_CHARS = 200_000
MAX_ENTRIES = 20              # 最多解析多少个包内文件
MAX_ENTRY_BYTES = 8 * 1024 * 1024   # 单条目解压上限
MAX_TOTAL_BYTES = 24 * 1024 * 1024  # 解压总量上限

#: 可以直接按文本解码的抽取 kind（其余走 files.extract_file 的二进制通道）
_TEXT_KINDS = frozenset({
    "text", "code", "html", "rtf", "json", "yaml", "xml", "table_text",
    "subtitle", "ipynb",
})
#: 需要落临时文件、交给通用抽取器的二进制分组
_BINARY_GROUPS = (GROUP_DOCUMENT, GROUP_SHEET, GROUP_SLIDE, GROUP_EBOOK)

#: 包内「不是资料」的元数据条目：macOS 的 __MACOSX/._* 资源叉、Windows 的
#: Thumbs.db / desktop.ini 等。真实 tar/zip 里几乎必然出现，抽出来只是一堆乱码，
#: 直接静默跳过（不占条目名额，也不打扰用户）。
_JUNK_NAMES = frozenset({
    ".ds_store", "ds_store", "thumbs.db", "desktop.ini", ".localized", "icon\r",
})
_JUNK_PREFIXES = ("__macosx/", "pax_global_header")
#: AppleDouble 资源叉的文件名特征：`._讲义.txt`（tarfile 会把开头的点剥掉，
#: 变成 `_讲义.txt`，所以两种写法都要认；但 `_笔记.txt` 这种正常下划线文件名要放过，
#: 因此只在扩展名是常见文档类型时才当资源叉）
_JUNK_LEAF = re.compile(
    r"^\._[^/]*$|^_[^/]*\.(txt|md|pdf|docx?|pptx?|xlsx?|csv|png|jpe?g|zip|rtf)$", re.IGNORECASE
)

#: 解压过程中可能出现的异常（坏数据、截断、不是真的压缩流）。
#: zlib.error 必须在内：gzip 的 CRC/块结构损坏抛的是它，不是 OSError——
#: 漏掉就会变成用户看到的「解析失败」，而不是「文件可能已损坏」。
_DECOMPRESS_ERRORS = (OSError, EOFError, ValueError, lzma.LZMAError, zlib.error)

#: 压缩包家族的全部 kind（判断「这个条目是不是又一个压缩包」用）
_ARCHIVE_KINDS = frozenset({
    KIND_ZIP, KIND_TAR, KIND_GZIP, KIND_BZ2, KIND_XZ, KIND_7Z, KIND_RAR,
    KIND_DMG, KIND_ZSTD,
})

#: 压缩魔数（与 formats.py 的嗅探表同源，这里只负责解压）
_GZIP_MAGIC = b"\x1f\x8b"
_BZIP2_MAGIC = b"BZh"
_XZ_MAGIC = b"\xfd7zXZ\x00"
#: 压缩魔数 → (名称, 打开函数)。三个打开器的首个位置参数都是 `filename`，
#: 必须显式用 `fileobj=` 传文件对象（传错会静默变成「打不开的压缩文件」）。
_COMPRESS_OPENERS: tuple[tuple[bytes, str, Callable[[BinaryIO], BinaryIO]], ...] = (
    (_GZIP_MAGIC, "gzip", lambda h: gzip.GzipFile(fileobj=h)),
    (_BZIP2_MAGIC, "bzip2", lambda h: bz2.BZ2File(h)),
    (_XZ_MAGIC, "xz", lambda h: lzma.LZMAFile(h)),
)


# ---------------------------------------------------------------------------
# 上限保护读取流：解压炸弹的「总量」闸门就在这一层
# ---------------------------------------------------------------------------

class _LimitReached(Exception):
    """解压总量超过上限（不是错误，是要给用户一句说明）。"""


class LimitReader:
    """给已解压的流套一层总量上限：超限抛 `_LimitReached`，读到 EOF 返回空。"""

    def __init__(self, handle: BinaryIO, limit: int):
        self._handle = handle
        self._limit = limit
        self._read = 0

    def read(self, size: int = -1) -> bytes:
        if self._read >= self._limit:
            raise _LimitReached
        want = size if size is not None and size >= 0 else 64 * 1024
        want = min(want, self._limit - self._read)
        data = self._handle.read(want)
        self._read += len(data)
        return data

    def close(self) -> None:
        try:
            self._handle.close()
        except Exception:      # pragma: no cover - 关闭失败无需打扰用户
            pass


@contextmanager
def open_stream_reader(source: "bytes | Path", magic: bytes) -> Iterator[LimitReader]:
    """按压缩魔数打开**流式解压**读取器（只读前 MAX_TOTAL_BYTES 字节）。

    `source` 可以是文件路径（大文件不整载入内存）或字节串（包内条目）。
    调用方负责理解「读到的可能被截断」。
    """
    opener = next((fn for mg, _name, fn in _COMPRESS_OPENERS if mg == magic), None)
    if opener is None:
        raise ValueError("unknown compression magic")
    handle = io.BytesIO(bytes(source)) if isinstance(source, (bytes, bytearray)) else open(source, "rb")
    try:
        stream = opener(handle)
    except Exception:
        handle.close()
        raise
    reader = LimitReader(stream, MAX_TOTAL_BYTES)
    try:
        yield reader
    finally:
        reader.close()
        try:
            handle.close()
        except Exception:      # pragma: no cover
            pass


# ---------------------------------------------------------------------------
# 统一的「包内条目」抽象：zip / tar / 单文件压缩流都归一到同一条流水线
# ---------------------------------------------------------------------------

@dataclass
class ArchiveEntry:
    """一条待解析的包内条目（`reader` 是惰性的，读完即释放）。

    `note` 非空表示这不是一条真条目，而是一句「这个文件为什么被跳过」的说明
    （加密条目、超量提示等），遍历时直接记进 `skipped` 而不去读它。
    """

    name: str
    size: int
    reader: Callable[[], bytes]
    note: str = ""
    silent: bool = False         # 只计数不进提示（例如「包内共 N 个文件」）

    def read(self, limit: int) -> bytes:
        if self.size > limit:
            raise ValueError("oversized")
        raw = self.reader()
        if len(raw) > limit:
            raise ValueError("oversized")
        return raw


@dataclass
class WalkState:
    """遍历过程中的计数与说明文案。"""

    total: int = 0
    used: int = 0
    skipped: list[str] = field(default_factory=list)


#: 上传接口把文件存成 `<uuid>.<后缀>`，单文件压缩流（`讲义.txt.gz`）解压后的名字
#: 会变成那串 uuid。上传接口用 `original_name()` 把**真实文件名**放进来，
#: 抽取结果里就还是「讲义.txt」而不是「0d8d9dee…」。
_NAME_HINT: ContextVar[str] = ContextVar("studyos_archive_name_hint", default="")


@contextmanager
def original_name(filename: str):
    """在 with 块内告诉压缩包抽取器「这个文件的原始名字」（上传接口用）。"""
    token = _NAME_HINT.set(filename or "")
    try:
        yield
    finally:
        _NAME_HINT.reset(token)


def _clean_entry_name(name: str) -> str:
    """去掉 tar 里常见的 ``./`` 前缀（只去前缀，不吃掉真正的点号文件名）。"""
    clean = name.replace("\\", "/").strip()
    while clean.startswith("./"):
        clean = clean[2:]
    return clean


def _is_junk_name(name: str) -> bool:
    """包内条目是不是操作系统元数据（macOS 资源叉 / Windows 缩略图缓存）。"""
    clean = _clean_entry_name(name).lower()
    if not clean:
        return True
    if clean in _JUNK_NAMES or clean.startswith(_JUNK_PREFIXES):
        return True
    leaf = clean.rsplit("/", 1)[-1]
    return bool(_JUNK_LEAF.match(leaf))    # ._讲义.txt / _讲义.txt 这类资源叉


def _iter_zip(path: Path) -> Iterator[ArchiveEntry]:
    with zipfile.ZipFile(path) as zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if len(infos) > MAX_ENTRIES * 4:
            yield _note_entry(f"包内共 {len(infos)} 个文件，仅解析前 {MAX_ENTRIES} 个")
        for info in infos:
            name = info.filename
            if _is_junk_name(name):
                continue
            if info.flag_bits & 0x1:                 # 加密条目
                yield _skip_entry(f"{text_slice(name, 80)}（已加密）")
                continue

            def reader(info=info) -> bytes:
                with zf.open(info) as handle:
                    return handle.read(MAX_ENTRY_BYTES + 1)

            yield ArchiveEntry(name, info.file_size, reader)


def _tar_stream_mode(head: bytes) -> str:
    """文件头 → tarfile 的**流式**模式（`r|gz` 等）。

    显式指定压缩层，不用 `r|` 的自动识别：部分 Python 版本（3.14 实测）的
    `r|` 对 gzip 流会误报「truncated header」，而压缩层本来就在魔数里写着。
    """
    if head.startswith(_GZIP_MAGIC):
        return "r|gz"
    if head.startswith(_BZIP2_MAGIC):
        return "r|bz2"
    if head.startswith(_XZ_MAGIC):
        return "r|xz"
    return "r|"


def _iter_tar(path: Path) -> Iterator[ArchiveEntry]:
    """tar 家族：**流式**读取（``r|gz`` 等），只有当前条目在内存里。

    几十 GB 的 tar.gz 也不会撑爆内存，代价是拿到下一条时上一条的句柄已失效，
    所以 `reader` 必须在循环里立即消费。
    """
    try:
        handle = open(path, "rb")
    except OSError:
        return
    mode = _tar_stream_mode(handle.read(8))
    try:
        handle.seek(0)
        try:
            tf = tarfile.open(fileobj=handle, mode=mode)
        except (tarfile.TarError, OSError, EOFError, ValueError):
            handle.close()
            return
        with tf:                      # tarfile 不会关掉我们传进去的文件对象
            last = None
            try:
                for info in tf:
                    last = info
                    if info.isdir() or not info.isfile():
                        continue
                    name = _clean_entry_name(info.name)
                    if not name or _is_junk_name(name):
                        continue

                    def reader(tf=tf, info=info) -> bytes:
                        handle = tf.extractfile(info)
                        if handle is None:
                            return b""
                        with handle:
                            return handle.read(MAX_ENTRY_BYTES + 1)

                    yield ArchiveEntry(name, info.size, reader)
            except (tarfile.TarError, OSError, EOFError, ValueError):
                # 流式解压到一半坏了：保留已经读到的条目，只说明这一条
                if last is not None:
                    yield _skip_entry(f"{text_slice(last.name, 80)}（压缩数据在读取时中断）")
    finally:
        try:
            handle.close()
        except OSError:              # pragma: no cover
            pass


def _inner_stream_name(path: Path, detected=None) -> str:
    """单文件压缩流解压后的条目名：优先原始文件名，缺失时给个像样的名字。

    上传落盘后文件名是 uuid，`path` 给不出有用信息；上传接口会用
    `original_name()` 把真实文件名放进来（`讲义.txt.gz` → 「讲义.txt」）。
    万一没有提示（例如把资料附件重新抽一次），至少不要显示一串 uuid。
    """
    name = Path(_NAME_HINT.get() or path.name).name
    inner = name.rsplit(".", 1)[0] if "." in name.lstrip(".") else name
    if inner.lower().endswith(".tar"):
        inner = inner[:-4]
    if inner and "." in inner.lstrip("."):
        return inner                    # 原名里带后缀（讲义.txt）：直接用
    spec = getattr(detected, "spec", None)
    label = getattr(spec, "label", "") or "解压内容"
    return f"{inner}（{label}）" if inner else label


def _iter_single_stream(path: Path, magic: bytes, *, detected=None) -> Iterator[ArchiveEntry]:
    """单文件压缩流（.gz/.bz2/.xz）→ 恰好一个条目：解压后的内容本身。"""
    inner = _inner_stream_name(path, detected)

    def reader() -> bytes:
        try:
            with open_stream_reader(path, magic) as reader:
                return reader.read(MAX_ENTRY_BYTES + 1)
        except _LimitReached:
            return b""                        # 解压后超过总量上限：当成读不出内容
        except _DECOMPRESS_ERRORS:
            return b""

    yield ArchiveEntry(inner, 0, reader)


def _skip_entry(message: str) -> ArchiveEntry:
    """借条目抽象传一条「跳过」说明（`note` 非空时遍历器不会去读它）。"""
    def reader() -> bytes:
        raise ValueError("skip")

    return ArchiveEntry("", 0, reader, note=message)


def _note_entry(message: str) -> ArchiveEntry:
    """同上，但只在遍历时计数、不出现在用户提示里。"""
    return ArchiveEntry("", 0, lambda: b"", note=message, silent=True)


def _iter_compressed(path: Path, magic: bytes, detected=None) -> Iterator[ArchiveEntry]:
    """单文件压缩流：先解压头 512 字节，是 tar 就整体当 tar 展开，否则当一个文档抽。

    只看外壳扩展名是分不清 `课程.tar.gz`（整包）与 `讲义.txt.gz`（单文档）的，
    所以要真解一小段看里面的结构。解不开就是「文件损坏」——这一层能把话说准，
    不必等条目层报一句含糊的「读取失败」。
    """
    from .formats import is_tar

    try:
        with open_stream_reader(path, magic) as reader:
            probe = reader.read(512)
    except (_LimitReached, *_DECOMPRESS_ERRORS):
        return iter([_skip_entry("压缩文件无法读取，可能已损坏")])
    if is_tar(probe):
        return _iter_tar(path)
    return _iter_single_stream(path, magic, detected=detected)


#: 各压缩包 kind → 条目迭代器（新增可解的格式只需在这里挂一行）
ARCHIVE_READERS: dict[str, Callable[[Path], Iterator[ArchiveEntry]]] = {
    KIND_ZIP: _iter_zip,
    KIND_TAR: _iter_tar,
}

#: 单文件压缩流的 kind → 压缩魔数（决定用哪个标准库解码器）
_COMPRESS_KIND_MAGIC: dict[str, bytes] = {
    KIND_GZIP: _GZIP_MAGIC,
    KIND_BZ2: _BZIP2_MAGIC,
    KIND_XZ: _XZ_MAGIC,
}


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def extract_archive(path: Path, *, detected=None, depth: int = 0) -> ExtractedFile:
    """压缩包 → 合并文本（按真实容器格式分派，`depth>0` 表示已在压缩包内）。"""
    from .formats import detect_format, normalize_ext

    info = detected or detect_format(path.name, path=path)
    kind = info.kind
    reader = ARCHIVE_READERS.get(kind)
    if reader is not None:
        entries = reader(path)
    elif kind in _COMPRESS_KIND_MAGIC:
        magic = _magic_of(path) or _COMPRESS_KIND_MAGIC[kind]
        entries = _iter_compressed(path, magic, detected=info)
    else:
        # 登记表里标了 supported=False 的格式（7z/rar/dmg/zst）：给可操作指引
        label = info.spec.label or "压缩包"
        note = info.spec.note or "请先解压，再上传包内的课件/讲义"
        return ExtractedFile("", warnings=[f"{label}本机暂不支持解包：{note}"])

    return _walk_entries(entries, kind=kind, kind_hint=normalize_ext(path.name), depth=depth)


def _magic_of(path: Path) -> bytes:
    """文件头命中的压缩魔数（认不出返回 b""）。"""
    try:
        with open(path, "rb") as f:
            head = f.read(8)
    except OSError:
        return b""
    for magic, _name, _fn in _COMPRESS_OPENERS:
        if head.startswith(magic):
            return magic
    return b""


def _walk_entries(
    entries: Iterator[ArchiveEntry], *, kind: str, kind_hint: str = "", depth: int = 0,
) -> ExtractedFile:
    """把条目流展开成一份合并文本（三重上限 + 只展开一层都在这里）。"""
    from .files import extract_file   # 延迟导入，避免循环依赖
    from .formats import detect_format

    blocks: list[str] = []
    chars = 0
    read_bytes = 0            # 实际解压出来的字节数：区分「空包」与「数据坏了」
    remaining = 0             # 触到上限时还剩多少条目没看（说明「不是没有，是没读完」）
    state = WalkState()
    try:
        for entry in entries:
            if entry.note:
                if not entry.silent:
                    state.skipped.append(entry.note)
                continue
            if state.used >= MAX_ENTRIES or chars >= MAX_TEXT_CHARS:
                # 数一下还剩多少条：包内 200 个文件只解析 20 个时要说清是上限，
                # 否则用户以为「我的资料没被读出来」
                remaining = 1 + sum(1 for _ in entries)
                break
            if entry.size > MAX_ENTRY_BYTES:
                state.skipped.append(f"{text_slice(entry.name, 80)}（超过单文件上限）")
                continue
            try:
                raw = entry.read(MAX_ENTRY_BYTES)
            except _LimitReached:
                state.skipped.append("解压总量超过上限，其余文件已跳过")
                break
            except (*_DECOMPRESS_ERRORS, zipfile.BadZipFile, tarfile.TarError,
                    RuntimeError, NotImplementedError):
                state.skipped.append(f"{text_slice(entry.name, 80)}（读取失败）")
                continue
            if len(raw) > MAX_ENTRY_BYTES:
                state.skipped.append(f"{text_slice(entry.name, 80)}（解压后过大）")
                continue
            state.total += len(raw)
            read_bytes += len(raw)
            if state.total > MAX_TOTAL_BYTES:
                state.skipped.append("解压总量超过上限，其余文件已跳过")
                break
            if not raw:
                continue
            detected = detect_format(entry.name, data=raw)
            if detected.kind == KIND_UNKNOWN:
                state.skipped.append(f"{text_slice(entry.name, 80)}（类型未识别）")
                continue
            if not detected.supported:
                state.skipped.append(
                    f"{text_slice(entry.name, 80)}（{detected.spec.label}本机不支持抽取）"
                )
                continue
            body = _entry_text(entry.name, raw, detected, depth, extract_file)
            if not body and detected.kind in _ARCHIVE_KINDS:
                state.skipped.append(f"{text_slice(entry.name, 80)}（嵌套压缩包只展开一层）")
                continue
            if not body and detected.group == GROUP_IMAGE:
                # 图片本该走 OCR：要么 OCR 不可用，要么识别不出文字
                state.skipped.append(
                    f"{text_slice(entry.name, 80)}（图片未识别出文字，可单独上传走 OCR）"
                )
                continue
            if not body:
                state.skipped.append(f"{text_slice(entry.name, 80)}（未抽取到文本）")
                continue
            blocks.append(f"[包内文件] {text_slice(entry.name, 120)}\n{body}")
            chars += len(body)
            state.used += 1
    except (*_DECOMPRESS_ERRORS, tarfile.TarError):
        # 迭代器本身在中途坏了：已抽到的内容照常返回，只补一句说明
        state.skipped.append("压缩包读取中断，可能不完整")

    if not blocks:
        label = _label_of(kind, kind_hint)
        if not read_bytes:
            # 一个字节都没解出来：是数据本身坏了，别报成「没有可解析的文档」
            return ExtractedFile("", warnings=[f"{label}无法读取，可能已损坏或不是标准压缩包"])
        reasons = list(state.skipped[:5])
        if remaining:
            reasons.append(f"包内还有 {remaining} 个文件未解析（每次最多解析 {MAX_ENTRIES} 个）")
        note = "；".join(reasons)
        return ExtractedFile(
            "", warnings=[f"{label}里没有可解析的文档{('：' + note) if note else ''}"]
        )
    result = ExtractedFile(join_blocks(blocks))
    skipped = list(state.skipped[:5])
    if remaining:
        skipped.append(f"包内还有 {remaining} 个文件未解析（每次最多解析 {MAX_ENTRIES} 个）")
    if skipped:
        result.with_warning(f"{_label_of(kind, kind_hint)}内部分文件已跳过：" + "；".join(skipped))
    return result


def _label_of(kind: str, kind_hint: str = "") -> str:
    """提示文案里的容器名：优先用登记表里的中文名（「TAR.GZ 压缩包」）。"""
    from .formats import spec_for

    spec = spec_for(kind_hint)
    if spec is not None and spec.kind == kind:
        return spec.label
    return {
        KIND_TAR: "TAR 压缩包", KIND_GZIP: "GZIP 压缩文件", KIND_BZ2: "BZIP2 压缩文件",
        KIND_XZ: "XZ 压缩文件",
    }.get(kind, "压缩包")


def _entry_text(name: str, raw: bytes, detected, depth: int, extract_file) -> str:
    """单个包内条目 → 文本（文本类直接解码，二进制类落临时文件走通用抽取）。"""
    if detected.group in (GROUP_MEDIA, GROUP_IMAGE):
        return ""            # 包内图片/音视频不在这里展开（OCR 交给用户单独上传）
    if depth > 0 and detected.kind in _ARCHIVE_KINDS:
        return ""            # 不展开嵌套压缩包（压缩包套压缩包 = 指数放大）
    if detected.kind in _ARCHIVE_KINDS:
        # 包内压缩包：交给统一的压缩包通道（单文件 gz/bz2/xz 值得展开，
        # 但其中的嵌套包会因为 depth 递增而被拦住）
        return _archived_entry_text(name, raw, detected, depth, extract_file)
    if detected.kind in _TEXT_KINDS:
        text, _ = decode_text(raw)
        return clean_text(text, 60_000)
    if detected.group in _BINARY_GROUPS:
        suffix = detected.spec.ext
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(raw)
            tmp_path = Path(tmp.name)
        try:
            # allow_ocr=False：包内 PDF 不重复跑 OCR（上传通道会按需再抽），
            # 也让压缩包抽取保持「纯 CPU 快路径」
            return clean_text(extract_file(tmp_path, allow_ocr=False).text, 80_000)
        finally:
            tmp_path.unlink(missing_ok=True)
    return ""


def _archived_entry_text(name: str, raw: bytes, detected, depth: int, extract_file) -> str:
    """包内压缩包 → 文本：单文件压缩流（gz/bz2/xz）解压后按真实内容再抽一次。

    只展开「压缩一层」的情况（`讲义.txt.gz`）；解压出来还是 tar 归档的
    （`套娃.tar.gz`）就此打住，避免压缩包套压缩包的指数放大。
    """
    if detected.kind not in (KIND_GZIP, KIND_BZ2, KIND_XZ):
        return ""
    magic = next((mg for mg, _n, _fn in _COMPRESS_OPENERS if raw.startswith(mg)), b"")
    if not magic:
        return ""
    from .formats import detect_format, is_tar

    with open_stream_reader(raw, magic) as reader:
        try:
            inner = reader.read(MAX_ENTRY_BYTES + 1)
        except (_LimitReached, *_DECOMPRESS_ERRORS):
            return ""
    if not inner or len(inner) > MAX_ENTRY_BYTES:
        return ""
    if is_tar(inner):
        return ""            # 里层是 tar 归档：属于嵌套压缩包，不再展开
    inner_name = name.rsplit(".", 1)[0] or name
    inner_detected = detect_format(inner_name, data=inner)
    if inner_detected.kind == KIND_UNKNOWN or not inner_detected.supported:
        return ""
    return _entry_text(inner_name, inner, inner_detected, depth + 1, extract_file)


def extract_zip(path: Path, *, depth: int = 0) -> ExtractedFile:
    """ZIP → 合并文本（保留旧入口名，等价于 `extract_archive`）。"""
    return _walk_entries(_iter_zip(path), kind=KIND_ZIP, kind_hint=".zip", depth=depth)


__all__ = [
    "ARCHIVE_READERS", "ArchiveEntry", "LimitReader", "MAX_ENTRIES", "MAX_ENTRY_BYTES",
    "MAX_TEXT_CHARS", "MAX_TOTAL_BYTES", "extract_archive", "extract_zip",
    "open_stream_reader", "original_name",
]
