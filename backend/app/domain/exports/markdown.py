"""Markdown 导出：人可读 + 可勾选 + 可进 Git/打印。

时间展示一律按设备本地时区（存储为 UTC，见 timeutil.py）。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from app.infra.models import Material, ReviewPlan, Task
from app.infra.timeutil import local_datetime, localnow, utcnow


def _fmt_dt(dt: datetime) -> str:
    return local_datetime(dt).strftime("%Y-%m-%d %H:%M")


def _remaining(due: datetime) -> str:
    delta = due - utcnow()
    days = delta.days
    if days < 0:
        return f"已逾期 {-days} 天"
    if days == 0:
        return f"还剩 {int(delta.total_seconds() // 3600)} 小时"
    return f"还剩 {days} 天"


def tasks_markdown(tasks: list[Task], course_names: dict[int, str]) -> str:
    lines = ["# StudyOS DDL 任务", "", f"导出时间：{localnow():%Y-%m-%d %H:%M}", ""]
    active = [t for t in tasks if not t.done]
    done = [t for t in tasks if t.done]
    lines.append(f"## 进行中（{len(active)}）")
    lines.append("")
    for t in sorted(active, key=lambda x: x.due_at):
        course = course_names.get(t.course_id or 0, "")
        prefix = f"**{course}** · " if course else ""
        lines.append(f"- [ ] {prefix}{t.title} — 截止 `{_fmt_dt(t.due_at)}`（{_remaining(t.due_at)}）")
    lines.append("")
    lines.append(f"## 已完成（{len(done)}）")
    lines.append("")
    for t in sorted(done, key=lambda x: x.due_at, reverse=True):
        course = course_names.get(t.course_id or 0, "")
        prefix = f"**{course}** · " if course else ""
        lines.append(f"- [x] {prefix}{t.title} — `{_fmt_dt(t.due_at)}`")
    return "\n".join(lines) + "\n"


def materials_markdown(materials: list[Material], course_names: dict[int, str]) -> str:
    lines = ["# StudyOS 资料目录", "", f"导出时间：{localnow():%Y-%m-%d %H:%M}", ""]
    grouped: dict[str, list[Material]] = {}
    for m in materials:
        grouped.setdefault(course_names.get(m.course_id or 0, "未分类"), []).append(m)
    diff = {"easy": "简单", "medium": "中等", "hard": "困难"}
    for course, items in grouped.items():
        lines.append(f"## {course}")
        lines.append("")
        lines.append("| 标题 | 类型 | 章节 | 难度 | 考点 | 标签 | 来源 | 创建时间 |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
        for m in items:
            lines.append(
                f"| {m.title} | {m.type} | {m.chapter or '—'} "
                f"| {diff.get(m.difficulty or '', '—')} | {'⭐' if m.exam_focus else '—'} "
                f"| {' '.join(m.tags or [])} | {m.source} | {_fmt_dt(m.created_at)} |"
            )
        lines.append("")
    return "\n".join(lines) + "\n"


def review_markdown(plans: list[ReviewPlan]) -> str:
    lines = ["# StudyOS 复习清单", "", f"导出时间：{localnow():%Y-%m-%d %H:%M}", ""]
    for p in plans:
        lines.append(f"## {p.title}")
        lines.append("")
        for s in p.sessions or []:
            mats = "；".join(s.get("materials", []) or [])
            lines.append(f"- [ ] **{s.get('date', '')}** · {s.get('course', '')}"
                         f"：{s.get('scope', '')}（约 {s.get('hours', '')} 小时）")
            due = "；".join(s.get("due_titles", []) or [])
            if due:
                lines.append(f"  - 到期间隔复习：{due}")
            spaced = "；".join(s.get("spaced_titles", []) or [])
            if spaced:
                lines.append(f"  - 计划间隔复习：{spaced}")
            if mats:
                lines.append(f"  - 对应资料：{mats}")
        lines.append("")
    return "\n".join(lines) + "\n"
