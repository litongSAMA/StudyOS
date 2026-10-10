"""AI 笔记提炼路由：提炼（无副作用）→ 保存 → 管理 → Markdown 导出。

与抽取 Agent 的交互模式一致：/refine 只计算不落库，用户预览满意后
由 POST /api/notes 保存为笔记；笔记可改、可删、可导出 Markdown 带走。

三个提炼入口共用同一条提炼管线（`agent/refine.py`）：

1. `POST /refine`——已有文本，或带全文的资料（material_id）；
2. `POST /refine-file`——直接上传文件：图片走离线 OCR，PDF / Word / Excel /
   PPT / ODF / 电子书 / 文本等走「资料入库」那一套抽取器（**内容嗅探在前，
   扩展名不可信**，改名的课件与无扩展名导出文件都能正确解析）；
3. `POST /refine-image`——`/refine-file` 的**图片专用兼容入口**（拒绝文档，
   提示文案为识图定制），老前端与外部调用方继续可用。
"""
from __future__ import annotations

import tempfile
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.agent.refine import NOTE_MODES, refine
from app.infra.db import get_db
from app.infra.ingest import archive, formats, ocr
from app.infra.ingest.files import extract_file
from app.infra.models import Material, Note
from ..schemas import (
    FileRefineResult,
    ImageRefineResult,
    NoteIn,
    NoteOut,
    NotePatch,
    RefineResult,
)
from app.api.serializers import note_out

router = APIRouter(prefix="/api/notes", tags=["notes"])

MODE_LABELS = {"keypoints": "重点笔记", "exam": "考点笔记"}
IMP_MARKS = {"high": "★", "medium": "●", "low": "·"}

MAX_IMAGE_BYTES = 20 * 1024 * 1024  # 识图提炼单图上限 20MB
MAX_DOC_BYTES = 50 * 1024 * 1024    # 文档提炼单文件上限 50MB（PDF/Word/Excel/PPT…）
MAX_SOURCE_TEXT = 20000  # 回传前端的抽取原文上限（切换模式重新提炼用）

#: 识图提炼只认图片时的提示（文档请走 /refine-file）
IMAGE_ONLY_HINT = (
    "识图提炼只支持图片文件（PNG/JPG/WEBP/BMP/GIF/TIFF/AVIF）；"
    "PDF / Word / Excel 等文档请用「文档提炼」，或到「资料入库」上传"
)


def _check_mode(mode: str) -> str:
    mode = (mode or "keypoints").strip().lower()
    if mode not in NOTE_MODES:
        raise HTTPException(422, f"笔记模式非法：{mode}（可选 keypoints/exam）")
    return mode


def _norm_points(points: list) -> list[dict]:
    """要点归一化：只保留非空要点，importance 限枚举，其余回落 medium。"""
    out: list[dict] = []
    for p in points or []:
        text = str(p.get("point", "") if isinstance(p, dict) else "").strip()
        if not text:
            continue
        imp = str(p.get("importance", "medium") if isinstance(p, dict) else "medium").lower()
        if imp not in ("high", "medium", "low"):
            imp = "medium"
        out.append({"point": text[:300], "importance": imp})
    return out[:20]


def render_note_md(n: NoteOut, index: int | None = None) -> str:
    """单条笔记 → Markdown（标题/概要/要点，元信息进引用块）。"""
    heading = f"# {n.title}" if index is None else f"## {index}. {n.title}"
    lines = [
        heading,
        "",
        f"> 模式：{MODE_LABELS.get(n.mode, n.mode)} ｜ 引擎：{'AI 模型' if n.engine == 'llm' else '离线规则'}"
        + (f" ｜ 来源：{n.material_title}" if n.material_title else "")
        + (f"（{n.course_name}）" if n.course_name else ""),
        f"> 创建时间：{n.created_at}",
        "",
    ]
    if n.summary:
        lines += ["## 概要", "", n.summary, ""]
    if n.points:
        lines += ["## 要点", ""]
        for p in n.points:
            mark = IMP_MARKS.get(p.importance, "●")
            lines.append(f"- **{mark}** {p.point}")
        lines.append("")
    return "\n".join(lines)


@router.post("/refine", response_model=RefineResult)
def refine_endpoint(body: dict, db: Session = Depends(get_db)):
    """从文本/资料全文提炼重点或考点笔记。不落库，供预览确认。

    body: { material_id?: int, text?: str, mode?: "keypoints"|"exam", title_hint?: str }
    """
    mode = _check_mode(str(body.get("mode", "keypoints")))
    text = str(body.get("text", "") or "").strip()
    title_hint = str(body.get("title_hint", "") or "").strip()
    material_id = body.get("material_id")
    if material_id is not None:
        m = db.get(Material, int(material_id))
        if not m:
            raise HTTPException(404, "资料不存在")
        text = text or (m.text or "").strip()
        title_hint = title_hint or m.title
    if not text:
        raise HTTPException(422, "文本为空：请提供 text，或指定带全文的资料 material_id")
    return refine(text, mode, title_hint)


def _spill_upload(file: UploadFile, suffix: str, limit: int, label: str = "文件") -> tuple[Path, int]:
    """上传流落到临时文件（边读边卡上限），返回 (路径, 字节数)；超限 413 且不留残文件。"""
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix or ".dat")
    tmp_path = Path(tmp.name)
    total = 0
    try:
        with tmp_path.open("wb") as f:
            while chunk := file.file.read(1024 * 1024):
                total += len(chunk)
                if total > limit:
                    raise HTTPException(
                        413, f"{label}过大（超过 {limit // (1024 * 1024)}MB），请压缩后重试"
                    )
                f.write(chunk)
    except Exception:
        tmp.close()
        tmp_path.unlink(missing_ok=True)
        raise
    tmp.close()
    return tmp_path, total


def _unsupported_hint(info: formats.Detected) -> str:
    """认得出来但抽不出正文（音视频）/ 完全认不出：给可操作的指引。"""
    if info.kind == formats.KIND_MEDIA:
        return (
            f"{info.spec.label}暂不支持离线转写（本机不联网、也不内置语音识别）；"
            "可以上传配套的课件/讲义（PDF、Word、PPT）提炼，或把录音转成文字后粘贴提炼"
        )
    if info.kind == formats.KIND_UNKNOWN:
        return f"未能识别这个文件的类型：{info.spec.note or formats.UNKNOWN_SPEC.note}"
    return f"{info.spec.label}暂不支持文本抽取：{info.spec.note or formats.UNKNOWN_SPEC.note}"


def _empty_hint(info: formats.Detected, extracted) -> str:
    """抽到空文本：按「图片 OCR / 扫描件 / 空文档」分口径给下一步动作。"""
    if info.kind == formats.KIND_IMAGE:
        return (
            "未能从图片中识别出文字：OCR 引擎未就绪（未安装 rapidocr-onnxruntime）"
            "或图片模糊/无文字。可换一张更清晰的图片重试，或直接粘贴文本提炼"
        )
    detail = "；".join(extracted.warnings)
    if extracted.needs_ocr:
        base = (
            f"未能从这份{info.spec.label}中抽取到文字：它看起来是扫描件（没有文本层），"
            "且本机 OCR 没有识别出内容。可换更清晰的版本，或直接粘贴文本提炼"
        )
    else:
        base = (
            f"未能从这份{info.spec.label}中抽取到文字：可能是空文件，或正文都是图片。"
            "可换一份带文字的文件，或直接粘贴文本提炼"
        )
    return f"{base}（{detail}）" if detail else base


def _refine_upload(
    tmp_path: Path,
    fname: str,
    mode: str,
    title_hint: str,
    *,
    size: int,
    images_only: bool = False,
) -> FileRefineResult:
    """临时文件 → 文本 → 提炼结果（图片 OCR / 文档抽取共用一条提炼管线）。

    只做计算不落库：调用方拿到预览，用户确认后才 `POST /api/notes` 保存。
    """
    info = formats.detect_format(fname, path=tmp_path)
    if images_only and info.kind != formats.KIND_IMAGE:
        raise HTTPException(422, IMAGE_ONLY_HINT)
    # 图片另有一条更严的体积线（OCR 前要整图解码，20MB 已是上限）
    if info.kind == formats.KIND_IMAGE and size > MAX_IMAGE_BYTES:
        raise HTTPException(
            413, f"图片过大（超过 {MAX_IMAGE_BYTES // (1024 * 1024)}MB），请压缩后重试"
        )
    if not info.supported or info.group in formats.REFINE_EXCLUDED_GROUPS:
        raise HTTPException(422, _unsupported_hint(info))

    # 抽取器内部已做「图片像素校验 / 扫描版 PDF 自动 OCR / 扩展名不一致提示」，
    # 这里直接复用资料入库的同一套实现，不再各写一份。
    # original_name：临时文件是 uuid 名，压缩包展开要靠它还原包内条目名。
    with archive.original_name(fname):
        extracted = extract_file(tmp_path, detected=info)
    text = (extracted.text or "").strip()
    if not text:
        raise HTTPException(422, _empty_hint(info, extracted))

    result = refine(text, mode, title_hint)
    return FileRefineResult(
        **result.model_dump(),
        source_text=text[:MAX_SOURCE_TEXT],
        ocr_available=ocr.ocr_available(),
        source_kind="image" if info.kind == formats.KIND_IMAGE else "document",
        file_name=fname,
        file_ext=info.spec.ext,
        format_label=info.spec.label,
        format_group=info.spec.group,
        format_group_label=info.spec.group_label,
        text_chars=len(text),
        truncated=len(text) > MAX_SOURCE_TEXT,
        warnings=list(extracted.warnings),
    )


@router.post("/refine-file", response_model=FileRefineResult)
def refine_file_endpoint(
    file: UploadFile = File(...),
    mode: str = Form("keypoints"),
    title_hint: str = Form(""),
):
    """文件提炼：上传 PDF / Word / Excel / PPT / 图片 / 文本等 → 抽正文 → 提炼笔记。

    multipart：file（≤50MB；图片另按 ≤20MB 与像素上限卡）、mode（keypoints/exam）、
    title_hint（可选标题提示，默认取去掉扩展名的文件名）。格式按**内容**判定，
    改名的课件（.ppt 实际是 .pptx）也能正确解析；扫描版 PDF 自动转 OCR。

    返回提炼结果 + 抽取原文（前端可核对、可修正、切换模式重新提炼无需再次上传）；
    音视频等本机无法转写的类型、以及抽不到正文的文件回 422 并说明下一步动作。
    """
    mode = _check_mode(mode)
    fname = Path(file.filename or "file").name
    tmp_path, size = _spill_upload(
        file, Path(fname).suffix.lower(), MAX_DOC_BYTES, label="文件"
    )
    try:
        return _refine_upload(
            tmp_path, fname, mode, title_hint or Path(fname).stem, size=size
        )
    finally:
        tmp_path.unlink(missing_ok=True)


@router.post("/refine-image", response_model=ImageRefineResult)
def refine_image_endpoint(
    file: UploadFile = File(...),
    mode: str = Form("keypoints"),
    title_hint: str = Form(""),
):
    """识图提炼（`/refine-file` 的图片专用兼容入口）：图片 → OCR → 提炼重点/考点笔记。

    文档请改用 `/refine-file`；此入口保留给老前端与外部调用方，行为不变。
    """
    mode = _check_mode(mode)
    fname = Path(file.filename or "image").name
    tmp_path, size = _spill_upload(
        file, Path(fname).suffix.lower(), MAX_IMAGE_BYTES, label="图片"
    )
    try:
        return _refine_upload(
            tmp_path, fname, mode, title_hint or fname, size=size, images_only=True
        )
    finally:
        tmp_path.unlink(missing_ok=True)


@router.post("", response_model=NoteOut, status_code=201)
def create_note(body: NoteIn, db: Session = Depends(get_db)):
    """保存一条提炼笔记（来自 /refine 的预览，或手动编写）。"""
    mode = _check_mode(body.mode)
    if body.material_id is not None and not db.get(Material, body.material_id):
        raise HTTPException(404, "资料不存在")
    note = Note(
        material_id=body.material_id,
        title=body.title.strip()[:300] or "未命名笔记",
        mode=mode,
        summary=(body.summary or "").strip(),
        points=_norm_points([p.model_dump() for p in body.points]),
        engine=body.engine if body.engine in ("rule", "llm") else "rule",
    )
    db.add(note)
    db.commit()
    db.refresh(note)
    return note_out(db, note)


@router.get("/export/md")
def export_all_md(db: Session = Depends(get_db)):
    """全部笔记导出为单个 Markdown 文件（数据可带走）。"""
    notes = db.execute(select(Note).order_by(Note.created_at)).scalars().all()
    outs = [note_out(db, n) for n in notes]
    body = "\n\n".join(render_note_md(n, i + 1) for i, n in enumerate(outs)) or "# 笔记\n\n暂无笔记"
    fname = f"studyos-notes-{date.today().isoformat()}.md"
    return Response(
        content=body,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.get("", response_model=list[NoteOut])
def list_notes(
    material_id: int | None = None,
    mode: str | None = None,
    limit: int = 200,
    db: Session = Depends(get_db),
):
    """笔记列表：可按来源资料/模式过滤，按创建时间倒序。"""
    q = select(Note)
    if material_id is not None:
        q = q.where(Note.material_id == material_id)
    if mode:
        q = q.where(Note.mode == _check_mode(mode))
    q = q.order_by(Note.created_at.desc()).limit(min(max(limit, 1), 500))
    return [note_out(db, n) for n in db.execute(q).scalars().all()]


@router.get("/{note_id}", response_model=NoteOut)
def get_note(note_id: int, db: Session = Depends(get_db)):
    n = db.get(Note, note_id)
    if not n:
        raise HTTPException(404, "笔记不存在")
    return note_out(db, n)


@router.get("/{note_id}/md")
def export_note_md(note_id: int, db: Session = Depends(get_db)):
    """单条笔记导出为 Markdown 文件。"""
    n = db.get(Note, note_id)
    if not n:
        raise HTTPException(404, "笔记不存在")
    out = note_out(db, n)
    fname = f"studyos-note-{note_id}.md"
    return Response(
        content=render_note_md(out),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.patch("/{note_id}", response_model=NoteOut)
def patch_note(note_id: int, body: NotePatch, db: Session = Depends(get_db)):
    n = db.get(Note, note_id)
    if not n:
        raise HTTPException(404, "笔记不存在")
    data = body.model_dump(exclude_unset=True)
    if "mode" in data:
        data["mode"] = _check_mode(str(data["mode"] or ""))
    if "title" in data:
        data["title"] = (data["title"] or "").strip()[:300] or n.title
    if "summary" in data:
        data["summary"] = (data["summary"] or "").strip()
    if "points" in data:
        data["points"] = _norm_points([p.model_dump() for p in body.points])
    for k, v in data.items():
        setattr(n, k, v)
    db.commit()
    db.refresh(n)
    return note_out(db, n)


@router.delete("/{note_id}", status_code=204)
def delete_note(note_id: int, db: Session = Depends(get_db)):
    n = db.get(Note, note_id)
    if not n:
        raise HTTPException(404, "笔记不存在")
    db.delete(n)
    db.commit()
