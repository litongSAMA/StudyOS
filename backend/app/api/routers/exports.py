"""导出路由：iCal / CSV / Markdown 三种格式 × DDL/复习清单/资料目录，支持**增量导出**。

资料目录的 iCal 是**真 iCal**（每条资料一个全天 VEVENT，锚定入库日期形成
「资料收集时间线」，元数据进 DESCRIPTION），不再是 Markdown 内容兜底。

增量导出怎么工作：
1. 每次导出都往 `export_records` 写一行流水，`until_at` = 本次导出覆盖到的时刻；
2. 下次带 `incremental=true` 时，以同 (kind, format) 最近一条记录的 `until_at` 作为
   `since` 起点，只导出这之后**有变更**的条目（`updated_at` 回落 `created_at`）；
3. 首次增量（没有历史基线）自动退化为全量，并记录基线；
4. 增量结果为空时**不写记录**（基线不前移，历史也不会被 0 条噪声塞满）；
5. `since=<ISO 时间>` 可显式指定起点（覆盖 incremental 推导），便于「导出本周变更」。

导出记录既是审计流水（在设置页可查看/清空），也是增量基线——不再是「写了从不读」的死表。
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.infra.db import get_db
from app.domain.exports import csv_export, ical, markdown
from app.infra.models import ExportRecord, Material, ReviewPlan, Task
from ..schemas import (
    ExportFormatStatus,
    ExportKindStatus,
    ExportRecordOut,
    ExportStatusOut,
)
from app.api.serializers import course_names
from app.infra.timeutil import iso_utc, parse_iso, utcnow

router = APIRouter(prefix="/api/export", tags=["export"])

MEDIA = {
    "ical": "text/calendar; charset=utf-8",
    "csv": "text/csv; charset=utf-8",
    "md": "text/markdown; charset=utf-8",
}
EXT = {"ical": ".ics", "csv": ".csv", "md": ".md"}

KINDS = ("tasks", "materials", "review")
KIND_LABEL = {"tasks": "DDL 任务", "materials": "资料目录", "review": "复习清单"}
KIND_MODEL = {"tasks": Task, "materials": Material, "review": ReviewPlan}
STEM = {"tasks": "studyos-tasks", "materials": "studyos-materials", "review": "studyos-review"}

MAX_RECORDS = 200


def _course_names(db: Session) -> dict[int, str]:
    return course_names(db)


# ---------------------------------------------------------------------------
# 增量基线
# ---------------------------------------------------------------------------


def _changed_after(model, since: datetime):
    """「自 since 之后有变更」：updated_at 优先，回落 created_at（老数据无 updated_at）。"""
    return func.coalesce(model.updated_at, model.created_at) > since


def _last_record(db: Session, kind: str, fmt: str | None = None) -> ExportRecord | None:
    stmt = select(ExportRecord).where(ExportRecord.kind == kind)
    if fmt:
        stmt = stmt.where(ExportRecord.format == fmt)
    stmt = stmt.order_by(ExportRecord.created_at.desc(), ExportRecord.id.desc()).limit(1)
    return db.execute(stmt).scalars().first()


def _baseline(rec: ExportRecord | None) -> datetime | None:
    if rec is None:
        return None
    return rec.until_at or rec.created_at


def _resolve_since(
    db: Session, kind: str, fmt: str, incremental: bool, since: str | None
) -> tuple[datetime | None, bool]:
    """返回 (since 起点, 是否按增量导出)。since 显式指定优先于 incremental 推导。"""
    if since and since.strip():
        try:
            return parse_iso(since.strip()), True
        except ValueError as e:
            raise HTTPException(422, "since 需为 ISO 时间，例如 2025-08-21T10:00:00Z") from e
    if not incremental:
        return None, False
    return _baseline(_last_record(db, kind, fmt)), True


def _count_changed(db: Session, kind: str, since: datetime | None) -> int:
    model = KIND_MODEL[kind]
    stmt = select(func.count(model.id))
    if since is not None:
        stmt = stmt.where(_changed_after(model, since))
    return int(db.execute(stmt).scalar_one())


# ---------------------------------------------------------------------------
# 取数与渲染
# ---------------------------------------------------------------------------


def _collect(db: Session, kind: str, since: datetime | None) -> list:
    model = KIND_MODEL[kind]
    order = {
        "tasks": Task.due_at.asc(),
        "materials": Material.created_at.asc(),
        "review": ReviewPlan.created_at.desc(),
    }[kind]
    stmt = select(model)
    if since is not None:
        stmt = stmt.where(_changed_after(model, since))
    return list(db.execute(stmt.order_by(order)).scalars().all())


def _render(db: Session, kind: str, fmt: str, rows: list) -> str | bytes:
    names = _course_names(db)
    if kind == "tasks":
        if fmt == "ical":
            return ical.build_tasks_ical(rows, names)
        if fmt == "csv":
            return csv_export.tasks_csv(rows, names)
        return markdown.tasks_markdown(rows, names)
    if kind == "materials":
        if fmt == "csv":
            return csv_export.materials_csv(rows, names)
        if fmt == "ical":
            # 真 iCal：资料入库时间线（全天事件），非 Markdown 兜底
            return ical.build_materials_ical(rows, names)
        return markdown.materials_markdown(rows, names)
    if fmt == "ical":
        return ical.build_review_ical(rows)
    if fmt == "csv":
        return csv_export.review_csv(rows)
    return markdown.review_markdown(rows)


def _filename(kind: str, fmt: str, incremental: bool, until: datetime) -> str:
    stem = STEM[kind]
    if incremental:
        stem = f"{stem}-incremental-{until:%Y%m%d-%H%M}"
    return f"{stem}{EXT[fmt]}"


@router.get("/records", response_model=list[ExportRecordOut])
def list_records(
    kind: str | None = Query(default=None, description="tasks / materials / review"),
    fmt: str | None = Query(default=None, description="ical / csv / md"),
    limit: int = 50,
    db: Session = Depends(get_db),
):
    """导出历史（最近在前）：谁、什么格式、覆盖哪段时间、导了多少条。"""
    stmt = select(ExportRecord)
    if kind:
        stmt = stmt.where(ExportRecord.kind == kind)
    if fmt:
        stmt = stmt.where(ExportRecord.format == fmt)
    stmt = stmt.order_by(ExportRecord.created_at.desc(), ExportRecord.id.desc())
    stmt = stmt.limit(max(1, min(limit, MAX_RECORDS)))
    return [ExportRecordOut.model_validate(r) for r in db.execute(stmt).scalars().all()]


@router.delete("/records")
def clear_records(
    kind: str | None = Query(default=None, description="只清某一类；留空清全部"),
    db: Session = Depends(get_db),
):
    """清空导出历史 = **重置增量基线**（下一次增量导出退化为全量）。"""
    stmt = delete(ExportRecord)
    if kind:
        if kind not in KINDS:
            raise HTTPException(404, "不支持的导出类型，可选 tasks/materials/review")
        stmt = stmt.where(ExportRecord.kind == kind)
    deleted = db.execute(stmt).rowcount or 0
    db.commit()
    return {"deleted": int(deleted)}


@router.get("/status", response_model=ExportStatusOut)
def export_status(db: Session = Depends(get_db)):
    """导出状态：每类数据的总条数、各格式上次导出时间、自那以来的变更条数。"""
    kinds: list[ExportKindStatus] = []
    for kind in KINDS:
        total = _count_changed(db, kind, None)
        last_any = _last_record(db, kind)
        fmts: list[ExportFormatStatus] = []
        for fmt in MEDIA:
            rec = _last_record(db, kind, fmt)
            baseline = _baseline(rec)
            fmts.append(ExportFormatStatus(
                format=fmt,
                last=ExportRecordOut.model_validate(rec) if rec else None,
                changed=_count_changed(db, kind, baseline),
            ))
        kinds.append(ExportKindStatus(
            kind=kind,
            label=KIND_LABEL[kind],
            total=total,
            last_export=ExportRecordOut.model_validate(last_any) if last_any else None,
            formats=fmts,
        ))
    records = db.execute(
        select(ExportRecord)
        .order_by(ExportRecord.created_at.desc(), ExportRecord.id.desc())
        .limit(20)
    ).scalars().all()
    return ExportStatusOut(
        kinds=kinds,
        records=[ExportRecordOut.model_validate(r) for r in records],
        now=utcnow(),
    )


@router.get("/{kind}/{fmt}")
def export_data(
    kind: str,
    fmt: str,
    incremental: bool = Query(default=False, description="只导出自上次同类同格式导出以来的变更"),
    since: str | None = Query(default=None, description="显式指定起点（ISO 时间），覆盖 incremental 推导"),
    record: bool = Query(default=True, description="是否写入导出记录（增量基线）"),
    db: Session = Depends(get_db),
):
    """导出 `kind`（tasks/materials/review）为 `fmt`（ical/csv/md），可选增量。

    响应头附带导出元信息：`X-Export-Count`（条数）、`X-Export-Since` / `X-Export-Until`
    （覆盖区间）、`X-Export-Incremental`、`X-Export-Record-Id`。
    """
    if kind not in KINDS:
        raise HTTPException(404, "不支持的导出类型，可选 tasks/materials/review")
    if fmt not in MEDIA:
        raise HTTPException(404, "不支持的格式，可选 ical/csv/md")

    since_at, is_incremental = _resolve_since(db, kind, fmt, incremental, since)
    until_at = utcnow()
    rows = _collect(db, kind, since_at)
    content = _render(db, kind, fmt, rows)

    rec_id: int | None = None
    # 空的增量导出不写记录：基线不前移（下次仍能补齐），历史也不被 0 条噪声污染
    if record and (rows or not is_incremental):
        rec = ExportRecord(
            format=fmt,
            kind=kind,
            file_path=_filename(kind, fmt, is_incremental, until_at),
            since_at=since_at,
            until_at=until_at,
            item_count=len(rows),
            incremental=is_incremental,
        )
        db.add(rec)
        db.commit()
        rec_id = rec.id

    if isinstance(content, str):
        content = content.encode("utf-8")
    return Response(
        content=content,
        media_type=MEDIA[fmt],
        headers={
            "Content-Disposition": f'attachment; filename="{_filename(kind, fmt, is_incremental, until_at)}"',
            "X-Export-Kind": kind,
            "X-Export-Format": fmt,
            "X-Export-Incremental": "1" if is_incremental else "0",
            "X-Export-Since": iso_utc(since_at) if since_at else "",
            "X-Export-Until": iso_utc(until_at),
            "X-Export-Count": str(len(rows)),
            "X-Export-Record-Id": str(rec_id) if rec_id else "",
        },
    )
