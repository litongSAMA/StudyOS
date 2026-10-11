"""CSV 导出：UTF-8 BOM 编码（Excel 打开中文不乱码）。

时间展示一律按设备本地时区（存储为 UTC，见 timeutil.py）。
"""
from __future__ import annotations

import csv
import io
from datetime import datetime

from ..models import Material, ReviewPlan, Task
from ..timeutil import local_datetime


def _fmt_dt(dt: datetime) -> str:
    return local_datetime(dt).strftime("%Y-%m-%d %H:%M") if dt else ""


def tasks_csv(tasks: list[Task], course_names: dict[int, str]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["ID", "课程", "任务", "类型", "截止时间", "状态", "提前提醒(小时)", "备注", "来源原文"])
    for t in tasks:
        w.writerow([
            t.id, course_names.get(t.course_id or 0, ""), t.title, t.kind,
            _fmt_dt(t.due_at), "已完成" if t.done else "进行中",
            " ".join(str(x) for x in (t.remind_offsets_h or [])),
            t.note, t.source_text,
        ])
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


DIFFICULTY_LABELS = {"": "", "easy": "简单", "medium": "中等", "hard": "困难"}


def materials_csv(materials: list[Material], course_names: dict[int, str]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["ID", "课程", "标题", "类型", "章节", "难度", "考试重点", "标签", "来源", "文件名", "创建时间"])
    for m in materials:
        w.writerow([
            m.id, course_names.get(m.course_id or 0, ""), m.title, m.type,
            m.chapter or "",
            DIFFICULTY_LABELS.get(m.difficulty or "", ""),
            "是" if m.exam_focus else "",
            " ".join(m.tags or []), m.source, m.file_name,
            _fmt_dt(m.created_at),
        ])
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


def review_csv(plans: list[ReviewPlan]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["计划", "日期", "课程", "复习范围", "掌握度(%)", "资料", "时长(小时)"])
    for p in plans:
        for s in p.sessions or []:
            mastery = s.get("mastery")
            w.writerow([
                p.title, s.get("date", ""), s.get("course", ""), s.get("scope", ""),
                "" if not isinstance(mastery, (int, float)) else round(mastery * 100),
                "；".join(s.get("materials", []) or []), s.get("hours", ""),
            ])
    return ("\ufeff" + buf.getvalue()).encode("utf-8")
