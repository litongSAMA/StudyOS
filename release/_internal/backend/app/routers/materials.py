"""资料路由：上传解析、列表过滤（SQL 下推 + 分页）、标签聚合、编辑、删除。"""
from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..agent.extractor import extract_from_filename
from ..agent.common import normalize_difficulty
from ..db import fts_sync_add, fts_sync_delete, fts_sync_update, get_db
from ..filestore import files_root, safe_unlink
from ..ingest import archive
from ..ingest.files import extract_file
from ..ingest.formats import KIND_IMAGE, detect_format
from ..materialquery import (
    MAX_FACETS,
    MaterialFilter,
    chapter_facets,
    page_headers,
    page_materials,
    tag_facets,
)
from ..models import Course, Material, MaterialReview
from ..schemas import (
    ChapterFacet,
    FormatInfo,
    MaterialIn,
    MaterialOut,
    MaterialPatch,
    SuggestionBundle,
    TagFacet,
)
from ..semantic import invalidate_material
from ..serializers import course_names, material_out

router = APIRouter(prefix="/api/materials", tags=["materials"])

MAX_UPLOAD_BYTES = 512 * 1024 * 1024  # 单文件上传上限 512MB

DIFFICULTIES = ("", "easy", "medium", "hard")


def _check_difficulty(v: str | None) -> None:
    """难度只接受固定枚举（其余 422），保证过滤与复习加权口径一致。"""
    if v is not None and (v or "").strip().lower() not in DIFFICULTIES:
        raise HTTPException(422, f"难度取值非法：{v}（可选 easy/medium/hard）")


@router.get("", response_model=list[MaterialOut])
def list_materials(
    response: Response,
    course_id: int | None = None,
    type: str | None = None,
    chapter: str | None = None,
    difficulty: str | None = None,
    exam_focus: bool | None = None,
    tag: str | None = None,
    tags: list[str] | None = Query(default=None, description="多标签过滤，可重复传或用逗号分隔"),
    tag_mode: str = Query(default="any", pattern="^(any|all)$", description="多标签：any=任一 / all=全部"),
    limit: int = 200,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    """资料列表：课程/类型/章节/难度/考试重点/标签过滤**全部在 SQL 内完成**，limit 作用于过滤之后。

    分页元信息在响应头：`X-Total-Count`（过滤后总数）/`X-Limit`/`X-Offset`/`X-Has-More`。
    """
    f = MaterialFilter.build(
        course_id=course_id, type=type, chapter=chapter, difficulty=difficulty,
        exam_focus=exam_focus, tag=tag, tags=tags, tag_mode=tag_mode,
    )
    page = page_materials(db, f, limit=limit, offset=offset)
    response.headers.update(page_headers(page))
    names = course_names(db)
    return [material_out(db, m, names) for m in page.items]


@router.get("/tags", response_model=list[TagFacet])
def list_tag_facets(
    course_id: int | None = None,
    type: str | None = None,
    limit: int = MAX_FACETS,
    db: Session = Depends(get_db),
):
    """标签聚合（标签云）：SQL `GROUP BY` 统计**全库**标签及其资料数，按热度倒序。"""
    f = MaterialFilter.build(course_id=course_id, type=type)
    return [TagFacet(tag=t, count=n) for t, n in tag_facets(db, f, limit=limit)]


@router.get("/chapters", response_model=list[ChapterFacet])
def list_chapter_facets(
    course_id: int | None = None,
    type: str | None = None,
    limit: int = MAX_FACETS,
    db: Session = Depends(get_db),
):
    """章节聚合：SQL `GROUP BY` 统计**全库**章节及其资料数，按章节号自然排序。"""
    f = MaterialFilter.build(course_id=course_id, type=type)
    return [ChapterFacet(chapter=c, count=n) for c, n in chapter_facets(db, f, limit=limit)]


@router.get("/{material_id}", response_model=MaterialOut)
def get_material(material_id: int, db: Session = Depends(get_db)):
    m = db.get(Material, material_id)
    if not m:
        raise HTTPException(404, "资料不存在")
    return material_out(db, m)


@router.patch("/{material_id}", response_model=MaterialOut)
def patch_material(material_id: int, body: MaterialPatch, db: Session = Depends(get_db)):
    m = db.get(Material, material_id)
    if not m:
        raise HTTPException(404, "资料不存在")
    if body.course_id is not None and not db.get(Course, body.course_id):
        raise HTTPException(404, "课程不存在")
    _check_difficulty(body.difficulty)
    data = body.model_dump(exclude_unset=True)
    if "difficulty" in data:
        data["difficulty"] = normalize_difficulty(data["difficulty"])
    if "chapter" in data:
        data["chapter"] = (data["chapter"] or "").strip()[:120]
    for k, v in data.items():
        setattr(m, k, v)
    db.flush()
    fts_sync_update(db, m)
    invalidate_material(db, m.id)
    db.commit()
    db.refresh(m)
    return material_out(db, m)


@router.delete("/{material_id}", status_code=204)
def delete_material(material_id: int, db: Session = Depends(get_db)):
    m = db.get(Material, material_id)
    if not m:
        raise HTTPException(404, "资料不存在")
    safe_unlink(m.file_path)  # 只允许删除 data/files 内的附件
    fts_sync_delete(db, material_id)
    invalidate_material(db, material_id)
    # 资料删了，它的间隔重复状态（掌握程度/复习历史）一并清理；
    # 先 flush 掉引用行，避免 FK 约束阻止 materials 删除
    for st in db.execute(
        select(MaterialReview).where(MaterialReview.material_id == material_id)
    ).scalars().all():
        db.delete(st)
    db.flush()
    db.delete(m)
    db.commit()


@router.post("", response_model=MaterialOut, status_code=201)
def create_material(body: MaterialIn, db: Session = Depends(get_db)):
    if body.course_id is not None and not db.get(Course, body.course_id):
        raise HTTPException(404, "课程不存在")
    _check_difficulty(body.difficulty)
    data = body.model_dump()
    data["difficulty"] = normalize_difficulty(data["difficulty"])
    data["chapter"] = (data["chapter"] or "").strip()[:120]
    m = Material(**data)
    db.add(m)
    db.flush()
    fts_sync_add(db, m)
    invalidate_material(db, m.id)
    db.commit()
    db.refresh(m)
    return material_out(db, m)


@router.post("/upload", response_model=SuggestionBundle)
def upload_material(
    file: UploadFile = File(...),
    course_hint: str = Form(""),
    db: Session = Depends(get_db),
):
    """上传文件：保存 → 按**内容**识别格式并抽取文本 → 返回解析建议。

    不直接落库——先返回 SuggestionBundle 供前端展示确认。
    格式不信任扩展名（`ingest.formats.detect_format`：魔数 + 容器结构），
    支持文档/表格/演示/电子书/Notebook/网页/RTF/字幕/代码/压缩包/图片 OCR，
    扫描版 PDF 自动栅格化后 OCR；图片先校验像素尺寸（单边 ≤12000px、
    总像素 ≤5000 万），防解压炸弹 OOM。
    """
    fname = Path(file.filename or "unnamed").name
    suffix = Path(fname).suffix.lower()
    store_dir = files_root()
    stored = store_dir / f"{uuid.uuid4().hex}{suffix}"
    total = 0
    try:
        with stored.open("wb") as f:
            while chunk := file.file.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        413, "文件过大（超过 512MB），请压缩后重试或分批上传"
                    )
                f.write(chunk)
    except HTTPException:
        stored.unlink(missing_ok=True)
        raise
    except Exception:
        stored.unlink(missing_ok=True)
        raise HTTPException(422, "文件保存失败，请重试")

    # 格式识别 + 文本抽取：识别结果同时用于「按内容选解析器」和回包展示
    detected = detect_format(fname, path=stored)
    try:
        # original_name：附件在磁盘上是 uuid 名，压缩包展开要靠它还原包内条目名
        # （`讲义.txt.gz` → 「讲义.txt」而不是一串 uuid，详见 ingest/archive.py）
        with archive.original_name(fname):
            ext = extract_file(stored, detected=detected)
    except HTTPException:
        stored.unlink(missing_ok=True)   # 图片尺寸/解码校验未通过：不留残留文件
        raise
    text = ext.text

    from ..agent.extractor import extract

    source = "screenshot" if detected.kind == KIND_IMAGE else "file"
    result = extract(db, text, course_hint or fname, source=source)
    # 文件名通道补充（短文件名里的日期/课程码，正文可能没有）
    fname_result = extract_from_filename(db, fname)
    seen = {t.title for t in result.tasks}
    for t in fname_result.tasks:
        if t.title not in seen:
            result.tasks.append(t)
    if fname_result.materials:
        result.materials = fname_result.materials[:1] + result.materials

    for warning in ext.warnings:
        result.warnings.append(warning)
    if ext.needs_ocr and not text:
        result.warnings.append("该文件未能抽取到文本（可能是扫描件），请补充标题/标签或手动录入")

    return SuggestionBundle(
        extract=result,
        file_name=fname,
        extracted_text_preview=text[:1500],
        stored_path=str(stored),
        format=FormatInfo(**detected.as_dict()),
        text_chars=len(text),
    )
