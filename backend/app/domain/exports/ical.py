"""iCal (RFC 5545) 导出：DDL 任务 → VTODO（带 VALARM），复习计划 → VEVENT，
资料目录 → 全天 VEVENT 时间线（每条资料锚定入库日期）。

UID 稳定（studyos-<实体>-<id>@studyos），重复导入日历不会重复。
时间：存储为 naive UTC → 输出为带本地时区的 aware datetime，
让日历客户端在任何时区都显示正确时刻。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from ics import Calendar, Event, Todo
from ics.alarm import DisplayAlarm

from app.infra.models import Material, ReviewPlan, Task
from app.infra.timeutil import local_date, local_datetime

PRODID = "-//StudyOS//个人学习管理//ZH"


def _utc_to_local(dt: datetime) -> datetime:
    """naive UTC（due_at 存储口径）→ 本地时区 aware（保证 iCal 偏移正确）。"""
    return local_datetime(dt) if dt else dt


def _local(dt: datetime) -> datetime:
    """naive 本地时间（复习计划的日期串）→ 本地时区 aware。"""
    return dt.astimezone() if dt else dt


def _uid(kind: str, id_: int) -> str:
    return f"studyos-{kind}-{id_}@studyos"


DIFFICULTY_LABELS = {"easy": "简单", "medium": "中等", "hard": "困难"}


def build_materials_ical(
    materials: list[Material], course_names: dict[int, str] | None = None
) -> str:
    """资料目录 → **真 iCal**（不再是 Markdown 兜底）：每条资料一个全天 VEVENT，
    锚定其本地入库日期，在日历里形成「资料收集时间线」；
    DESCRIPTION 携带课程/类型/章节/难度/考试重点/标签/来源等完整元数据。
    UID 稳定，重复导入日历不重复；空列表输出合法空 VCALENDAR（增量导出可用）。
    """
    cal = Calendar()
    cal.creator = PRODID
    course_names = course_names or {}
    for m in materials:
        ev = Event()
        ev.uid = _uid("material", m.id)
        course = course_names.get(m.course_id or 0, "")
        ev.name = f"[{course}] 资料·{m.title}" if course else f"资料·{m.title}"
        lines = [
            f"课程：{course or '未分类'}",
            f"类型：{m.type}",
        ]
        if m.chapter:
            lines.append(f"章节：{m.chapter}")
        lines.append(f"难度：{DIFFICULTY_LABELS.get(m.difficulty or '', '未标注')}")
        lines.append("考试重点：是" if m.exam_focus else "考试重点：否")
        if m.tags:
            lines.append("标签：" + " ".join(m.tags))
        lines.append(f"来源：{m.source}")
        ev.description = "\n".join(lines)
        ev.begin = local_date(m.created_at)
        ev.make_all_day()
        cal.events.add(ev)
    return cal.serialize()


def build_tasks_ical(tasks: list[Task], course_names: dict[int, str] | None = None) -> str:
    cal = Calendar()
    cal.creator = PRODID
    course_names = course_names or {}
    for t in tasks:
        todo = Todo()
        todo.uid = _uid("task", t.id)
        course = course_names.get(t.course_id or 0, "")
        todo.name = f"[{course}] {t.title}" if course else t.title
        todo.description = f"课程：{course}\n类型：{t.kind}\n" + (t.note or "")
        due = _utc_to_local(t.due_at)
        todo.due = due
        todo.begin = due - timedelta(hours=1)
        if t.done:
            todo.status = "COMPLETED"
        for h in sorted(set(t.remind_offsets_h or [24])):
            alarm = DisplayAlarm(
                trigger=timedelta(hours=-h),
                display_text=f"提醒：{todo.name} 还有 {h} 小时截止",
            )
            todo.alarms.append(alarm)
        cal.todos.add(todo)
    return cal.serialize()


def build_review_ical(plans: list[ReviewPlan]) -> str:
    cal = Calendar()
    cal.creator = PRODID
    for p in plans:
        for s in p.sessions or []:
            ev = Event()
            ev.uid = _uid("review", p.id * 1000 + (p.sessions or []).index(s))
            ev.name = f"复习·{s.get('course', '')}：{s.get('scope', '')}"
            desc = "；".join(s.get("materials", []) or [])
            due = "；".join(s.get("due_titles", []) or [])
            if due:
                desc += f"\n到期间隔复习：{due}"
            spaced = "；".join(s.get("spaced_titles", []) or [])
            if spaced:
                desc += f"\n计划间隔复习：{spaced}"
            ev.description = desc
            try:
                d = datetime.fromisoformat(s["date"])
            except (ValueError, KeyError):
                continue
            ev.begin = _local(d.replace(hour=19, minute=0))
            try:
                hours = float(s.get("hours", 2) or 2)
            except (TypeError, ValueError):
                hours = 2.0
            ev.duration = {"hours": max(1, int(round(hours)))}
            cal.events.add(ev)
    return cal.serialize()
