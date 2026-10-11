"""AI Agent 路由：抽取（无副作用）→ 确认 → 落库（apply）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..agent.extractor import extract, _to_bool
from ..agent.common import normalize_difficulty
from ..db import fts_sync_add, get_db
from ..filestore import is_inside_files
from ..models import Course, Material, Task
from ..schemas import (
    ApplyResult,
    ExtractResult,
    MaterialSuggestion,
    TaskSuggestion,
)
from ..semantic import invalidate_material
from ..serializers import course_names, material_out, task_out

router = APIRouter(prefix="/api/agent", tags=["agent"])


@router.post("/extract", response_model=ExtractResult)
def extract_endpoint(body: dict, db: Session = Depends(get_db)):
    """从任意文本抽取 DDL 任务与资料建议。不落库。"""
    text = str(body.get("text", ""))[:8000]
    course_hint = str(body.get("course_hint", ""))
    if not text.strip():
        raise HTTPException(422, "文本为空")
    return extract(db, text, course_hint)


