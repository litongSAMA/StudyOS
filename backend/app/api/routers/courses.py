"""课程路由：CRUD + 导入课表（iCal / JSON / Excel / CSV / ODS / PDF / 网页 / Word / 截图 → 课程 + 课表时段）。"""
from __future__ import annotations

import io
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.courseimport import (
    ImportParseError,
    guess_semester,
    merge_slots,
    parse_import_source,
    pick_color,
)
from app.domain.courseimport_ai import llm_extract_source
from app.domain.courseimport_sheet import (
    MAX_PDF_OCR_DPI,
    MAX_PDF_OCR_PAGES,
    SUPPORTED_SOURCES,
    PdfUnreadableError,
    _GRID_FORMATS,
    _grid_from_pdf,
    cleanup_dir,
    combine_grids,
    decode_upload_text,
    detect_kind,
    is_spreadsheet,
    parse_grid,
    parse_spreadsheet,
    pdf_pages_to_images,
    read_grid,
)
from app.infra.db import get_db
from app.infra.ingest import formats, ocr
from app.infra.ingest.ocrgrid import ocr_grid
from app.infra.models import Course, Material, Task
from ..schemas import (
    CourseImportApply,
    CourseImportEntry,
    CourseImportPreview,
    CourseImportResult,
    CourseIn,
    CourseOut,
    CoursePatch,
    CourseSlot,
)

router = APIRouter(prefix="/api/courses", tags=["courses"])

MAX_IMPORT_BYTES = 2 * 1024 * 1024  # 课表文件/粘贴内容上限 2MB

#: 来源格式中文名（前端优先展示它，缺失时回落到 format）
_FORMAT_LABELS = {
    "excel": "Excel 课表", "csv": "CSV 课表", "ics": "iCal 日历课表",
    "json": "JSON 课表", "pdf": "PDF 课表", "html": "网页课表",
    "word": "Word 课表", "ods": "ODS 课表", "image": "课表截图",
}


def _format_label(fmt: str, ai_assisted: bool = False) -> str:
    """来源格式 → 中文名；AI 兜底的结果要标出来，用户才知道这是模型读的。"""
    label = _FORMAT_LABELS.get(fmt or "", "")
    if not ai_assisted:
        return label
    return f"{label}（AI 辅助）" if label else "AI 辅助识别"



@router.get("", response_model=list[CourseOut])
def list_courses(db: Session = Depends(get_db)):
    return db.execute(select(Course).order_by(Course.name)).scalars().all()


@router.post("", response_model=CourseOut, status_code=201)
def create_course(body: CourseIn, db: Session = Depends(get_db)):
    exists = db.execute(select(Course).where(Course.name == body.name)).scalar_one_or_none()
    if exists:
        raise HTTPException(409, "课程已存在")
    c = Course(**body.model_dump())
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


@router.patch("/{course_id}", response_model=CourseOut)
def update_course(course_id: int, body: CoursePatch, db: Session = Depends(get_db)):
    c = db.get(Course, course_id)
    if not c:
        raise HTTPException(404, "课程不存在")
    if body.name is not None:
        clash = db.execute(
            select(Course).where(Course.name == body.name, Course.id != course_id)
        ).scalar_one_or_none()
        if clash:
            raise HTTPException(409, "同名课程已存在")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(c, k, v)
    db.commit()
    db.refresh(c)
    return c


@router.delete("/{course_id}", status_code=204)
def delete_course(course_id: int, db: Session = Depends(get_db)):
    c = db.get(Course, course_id)
    if not c:
        raise HTTPException(404, "课程不存在")
    # 不级联删除资料/任务（界面承诺过），只解除关联，否则外键约束直接 500
    for m in db.execute(
        select(Material).where(Material.course_id == course_id)
    ).scalars().all():
        m.course_id = None
    for t in db.execute(
        select(Task).where(Task.course_id == course_id)
    ).scalars().all():
        t.course_id = None
    db.flush()
    db.delete(c)
    db.commit()


# ---------------------------------------------------------------------------
# 导入课表：预览（解析 + 已存在标记，无副作用）→ 确认导入（创建/合并）
# ---------------------------------------------------------------------------

def _decode_upload(data: bytes) -> str:
    """课表文件字节 → 文本：UTF-8(带 BOM) 优先，回退 GB18030（部分教务系统导出编码）。"""
    return decode_upload_text(data)


def _looks_like_ics(text: str) -> bool:
    """文本是否像 iCalendar（避免把 CSV 课表误送进 iCal 解析器）。"""
    head = (text or "").lstrip()[:200].upper()
    return head.startswith("BEGIN:VCALENDAR") or "BEGIN:VEVENT" in head


def _image_ext(filename: str, data: bytes) -> str:
    """图片的规范扩展名（文件名或魔数任一命中即算图片）；不是图片返回 ""。

    扩展名不可信、魔数也不全：iPhone 传上来的 HEIC 可能被浏览器改名成 .jpg，
    而 AVIF/HEIC 又没有几位「人人认得」的魔数，所以两边都看，取能认出的那个。
    """
    declared = formats.normalize_ext(filename)
    if formats.is_image_ext(declared):
        return declared
    sniffed = formats.sniff(data)
    return sniffed if formats.is_image_ext(sniffed) else ""


def _to_ocr_png(data: bytes, ext: str) -> bytes:
    """非 PNG/JPEG 图片先转成 PNG：OCR 引擎不保证认 GIF/TIFF/AVIF，Pillow 认。

    转换失败就原样交给 OCR——那一步还有尺寸校验与「换张更清晰的图」提示兜底。
    """
    if ext in ("", ".png", ".jpg", ".jpeg"):
        return data
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as image:
            buf = io.BytesIO()
            image.convert("RGB").save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return data


def _parse_image(data: bytes, ext: str) -> dict:
    """课表截图/照片 → 条目：OCR 坐标重建网格 → 网格解析；失败走 LLM 智能兜底。"""
    if not formats.decoder_available(ext):
        raise ImportParseError(
            f"本机没有 {ext.lstrip('.').upper()} 图片的解码器：请先在手机或图片工具里"
            "转成 JPG/PNG 再上传（HEIC/HEIF 是 iPhone 的默认截图格式）"
        )
    if not ocr.ocr_available():
        raise ImportParseError(
            "图片课表需要 OCR 引擎（rapidocr-onnxruntime），当前环境不可用。"
            f"可改用 {SUPPORTED_SOURCES} 里的其它格式，或粘贴课表文本"
        )
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
            tmp.write(_to_ocr_png(data, ext))
            tmp_path = Path(tmp.name)
        struct = ocr_grid(tmp_path)
        grid_error: ImportParseError | None = None
        if struct["rows"]:
            try:
                parsed = parse_grid(struct["rows"], fmt="image")
                return {**parsed, "ai_assisted": False}
            except ImportParseError as e:
                grid_error = e  # 网格重建不理想 → 交给 LLM 兜底，不直接失败
        if struct["text"]:
            llm = llm_extract_source(struct["text"])
            if llm:
                return {**llm, "ai_assisted": True}
        raise grid_error or ImportParseError(
            "未能从图片里识别出课表内容：请换一张更清晰、无遮挡的课表截图重试，"
            f"或改用 {SUPPORTED_SOURCES}"
        )
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# PDF：文字层优先，扫描件（无文字层）渲染成图走 OCR
# ---------------------------------------------------------------------------

def _parse_pdf(data: bytes) -> dict:
    """PDF 课表 → 条目：先按文字层解析；扫描件渲染成图走 OCR，再退 AI 兜底。"""
    grid: list[list[str]] = []
    grid_error: ImportParseError | None = None
    try:
        grid = _grid_from_pdf(data)
    except PdfUnreadableError:
        raise  # 文件本身读不出来：渲染成图片同样会失败，直接给「另存为」提示
    except ImportParseError as e:
        grid_error = e
    if grid:
        try:
            parsed = parse_grid(grid, fmt="pdf")
            return {**parsed, "format": "pdf", "ai_assisted": False}
        except ImportParseError as e:
            grid_error = e
    if not ocr.ocr_available():
        if grid:
            # 有文字层，只是没解析出课表：网格解析的提示（缺星期表头/节次列）更准确
            raise grid_error or ImportParseError(f"没能从这个 PDF 里解析出课程；可改用 {SUPPORTED_SOURCES}")
        raise ImportParseError(
            "这个 PDF 没有可用的文字层（扫描版图片 PDF），需要 OCR 引擎"
            "（rapidocr-onnxruntime）才能识别，当前环境不可用："
            "请改用 .ics / Excel / CSV / 文本版 PDF，或把课表页另存为图片后按「课表截图」上传"
        ) from grid_error
    return _pdf_ocr(data)


def _pdf_ocr(data: bytes) -> dict:
    """扫描版 PDF → 逐页渲染 OCR：能重建成网格就走网格解析，否则把原文交给 LLM 兜底。"""
    tmp_dir, images = pdf_pages_to_images(
        data, max_pages=MAX_PDF_OCR_PAGES, dpi=MAX_PDF_OCR_DPI
    )
    try:
        if not images:
            raise ImportParseError(
                "无法把这个 PDF 渲染成图片（文件可能已损坏或被加密）："
                "请改用 .ics / Excel / CSV，或把课表页截图后按「课表截图」上传"
            )
        grids: list[list[list[str]]] = []
        texts: list[str] = []
        for image_path in images:
            struct = ocr_grid(image_path)
            if struct["rows"]:
                grids.append(struct["rows"])
            if struct["text"]:
                texts.append(struct["text"])
        grid_error: ImportParseError | None = None
        if grids:
            try:
                # OCR 网格按图片通道解析；对外的来源格式仍是 PDF（用户上传的就是 PDF）
                parsed = parse_grid(combine_grids(grids), fmt="image")
                return {**parsed, "format": "pdf", "ai_assisted": False}
            except ImportParseError as e:
                grid_error = e
        if texts:
            llm = llm_extract_source("\n".join(texts))
            if llm:
                return {**llm, "format": "pdf", "ai_assisted": True}
        raise grid_error or ImportParseError(
            "未能从这份 PDF 里识别出课表内容：请换更清晰的扫描件，"
            f"或改用 {SUPPORTED_SOURCES} 里的 .ics / Excel / CSV 导入"
        )
    finally:
        cleanup_dir(tmp_dir)  # 临时 PNG 必须清掉，无论成功失败


@router.post("/import/preview", response_model=CourseImportPreview)
async def preview_course_import(request: Request, db: Session = Depends(get_db)):
    """解析课表内容，返回待导入条目预览（只读，不落库，不做 OCR 以外的副作用）。

    两种提交方式（同一接口）：
    - 粘贴文本：JSON body `{"text": "..."}`（iCal / JSON 课表内容）；
    - 上传文件：multipart `file`（.ics / .json / .xlsx / .xls / .csv·TSV / .ods /
      .pdf / .html / .docx / 课表截图 PNG·JPG·WEBP·GIF·BMP·TIFF·AVIF…，
      UTF-8/GB18030 自动识别；格式按内容魔数与容器结构判断，不信任扩展名）。
    同名课程标记 exists（确认导入时按名合并，不重复建课）。
    """
    content_type = request.headers.get("content-type", "")
    raw: str | None = None
    data: bytes | None = None
    filename = ""
    if "form" in content_type:  # multipart（文件上传）或 urlencoded（文本表单）
        form = await request.form()
        up = form.get("file")
        if up is not None and hasattr(up, "read"):
            data = await up.read()
            filename = str(getattr(up, "filename", "") or "")
        elif form.get("text") is not None:
            raw = str(form["text"])
    else:
        try:
            body = await request.json()
        except Exception as e:
            raise HTTPException(422, "请求体应为 JSON：{\"text\": \"课表内容\"}") from e
        if isinstance(body, dict) and body.get("text") is not None:
            raw = str(body["text"])
    if data is not None:
        if len(data) > MAX_IMPORT_BYTES:
            raise HTTPException(413, f"课表文件过大（上限 {MAX_IMPORT_BYTES // 1024 // 1024}MB）")
        raw = _decode_upload(data)
    elif raw is not None:
        if len(raw.encode("utf-8")) > MAX_IMPORT_BYTES:
            raise HTTPException(413, f"课表内容过长（上限 {MAX_IMPORT_BYTES // 1024 // 1024}MB）")
    if raw is None:
        raise HTTPException(
            422,
            f"请上传课表文件（{SUPPORTED_SOURCES}），"
            "或提交 JSON {\"text\": \"课表内容\"}",
        )

    kind = detect_kind(data) if data is not None else ""
    try:
        if data is None:
            parsed = parse_import_source(raw)
        elif (ext := _image_ext(filename, data)):
            parsed = _parse_image(data, ext)
        elif kind == "pdf":
            parsed = _parse_pdf(data)
        elif kind != "csv":
            # 其余被嗅探出来的格式交给 read_grid 分派；.doc/.ppt/.zip 这类读不成网格的
            # 会在这里抛出「另存为 .docx / 先解压」这种可操作提示
            parsed = parse_grid(read_grid(data, kind), fmt=_GRID_FORMATS.get(kind, "excel"))
        elif _looks_like_ics(raw) or raw.lstrip().startswith("{"):
            parsed = parse_import_source(raw)
        elif is_spreadsheet(data):
            parsed = parse_spreadsheet(data, kind="csv")
        else:
            raise ImportParseError(
                f"无法识别的课表文件：支持 {SUPPORTED_SOURCES}，也可以直接粘贴课表文本"
            )
    except ImportParseError as e:
        raise HTTPException(422, f"课表解析失败：{e}") from e


    existing = {
        c.name: c.id for c in db.execute(select(Course)).scalars().all()
    }
    entries = []
    for i, e in enumerate(parsed["entries"]):
        eid = existing.get(e["name"])
        entries.append({
            **e,
            "exists": eid is not None,
            "existing_id": eid,
            "color": pick_color(i),
        })
    fmt = parsed["format"]
    ai_assisted = bool(parsed.get("ai_assisted"))
    return CourseImportPreview(
        format=fmt,
        format_label=_format_label(fmt, ai_assisted),
        term_start_date=parsed.get("term_start_date"),
        semester=parsed.get("semester") or "",
        ai_assisted=ai_assisted,
        entries=entries,
    )


@router.post("/import", response_model=CourseImportResult)
def apply_course_import(body: CourseImportApply, db: Session = Depends(get_db)):
    """确认导入：新课程创建（自动配色/学期锚点），同名课程合并课表与老师地点信息。"""
    if not body.entries:
        raise HTTPException(422, "没有勾选任何课程，请至少选择一门再导入")
    for e in body.entries:
        if not e.name.strip():
            raise HTTPException(422, "存在名称为空的课程条目，请检查课表内容")

    # 同批次同名条目先合并（不同 VEVENT/行拆出的同名课程）
    merged: dict[str, CourseImportEntry] = {}
    for e in body.entries:
        key = e.name.strip()
        if key in merged:
            prev = merged[key]
            prev.slots = [
                CourseSlot(**s)
                for s in merge_slots(
                    [s.model_dump() for s in prev.slots],
                    [s.model_dump() for s in e.slots],
                )
            ]
            prev.teacher = prev.teacher or e.teacher
            prev.location = prev.location or e.location
            prev.code = prev.code or e.code
        else:
            merged[key] = e

    from datetime import date

    semester = body.semester.strip() or guess_semester(
        date.fromisoformat(body.start_date) if body.start_date else None
    )
    start_date = body.start_date or None

    created: list[CourseOut] = []
    updated: list[CourseOut] = []
    existing_count = len(db.execute(select(Course.id)).scalars().all())
    for i, e in enumerate(merged.values()):
        c = db.execute(select(Course).where(Course.name == e.name)).scalar_one_or_none()
        if c is not None:
            changed = False
            for field, val in (
                ("teacher", e.teacher), ("location", e.location), ("code", e.code),
            ):
                if val and not getattr(c, field):
                    setattr(c, field, val)
                    changed = True
            if semester and not c.semester:
                c.semester = semester
                changed = True
            if start_date and not c.start_date:
                c.start_date = start_date
                changed = True
            new_slots = merge_slots(
                list(c.slots or []), [s.model_dump() for s in e.slots]
            )
            if new_slots != (c.slots or []):
                c.slots = new_slots
                changed = True
            if changed:
                db.flush()
                updated.append(CourseOut.model_validate(c))
        else:
            c = Course(
                name=e.name,
                code=e.code,
                semester=semester,
                start_date=start_date,
                color=pick_color(existing_count + i),
                teacher=e.teacher,
                location=e.location,
                slots=[s.model_dump() for s in e.slots],
            )
            db.add(c)
            db.flush()
            created.append(CourseOut.model_validate(c))
    db.commit()
    return CourseImportResult(created=created, updated=updated)
