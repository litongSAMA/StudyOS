from collections import defaultdict
from datetime import date, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo
import json

from .common import AgentError, canonical, identifier, number


def _class_hours(courses, day):
    intervals = []
    for course in courses:
        for slot in json.loads(course["slots"] or "[]"):
            if slot.get("weekday") != day.isoweekday():
                continue
            weeks = slot.get("weeks", [])
            if weeks and course.get("start_date"):
                week = (day - date.fromisoformat(course["start_date"])).days // 7 + 1
                if week not in weeks:
                    continue
            try:
                start = sum(int(value) * factor for value, factor in zip(slot["start"].split(":"), [60, 1]))
                end = sum(int(value) * factor for value, factor in zip(slot["end"].split(":"), [60, 1]))
                if 0 <= start < end <= 1440:
                    intervals.append((start, end))
            except (ValueError, KeyError):
                continue
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return sum(end - start for start, end in merged) / 60


def _proposal(service, con, tasks, sessions, courses, *, plan_id, hours_per_day, today_only=False):
    zone = ZoneInfo(service.timezone_name)
    today = service.clock().astimezone(zone).date()
    states = {row["material_id"]: dict(row) for row in con.execute("SELECT * FROM material_reviews")}
    materials = {row["id"]: dict(row) for row in con.execute("SELECT * FROM materials")}
    focus = [dict(row) for row in con.execute("SELECT * FROM focus_sessions WHERE mode='focus' ORDER BY started_at,id")]
    spent_by_date = defaultdict(float)
    budget_by_date = defaultdict(float)
    for record in focus:
        budget_day = datetime.fromisoformat(service._stored_time(record["started_at"])).astimezone(zone).date().isoformat()
        budget_by_date[budget_day] += record["duration_min"] / 60
        if plan_id and record["plan_id"] == plan_id:
            spent_by_date[record["plan_date"]] += record["duration_min"] / 60
    other_planned = defaultdict(float)
    other_spent = defaultdict(float)
    for record in focus:
        if record["plan_id"] != plan_id:
            other_spent[(record["plan_id"], record["plan_date"])] += record["duration_min"] / 60
    other_checks = []
    for row in con.execute("SELECT * FROM review_plans WHERE id IS NOT ?", (plan_id,)):
        row = dict(row)
        other_checks.append({"table": "review_plans", "id": row["id"], "before": row})
        for block in json.loads(row["sessions"]):
            if not block.get("completed"):
                spent_key = (row["id"], block["date"])
                already_worked = min(max(0, block["hours"]), other_spent[spent_key])
                other_spent[spent_key] -= already_worked
                other_planned[block["date"]] += max(0, block["hours"] - already_worked)
    completed, units, material_ids = [], [], set()
    for index, raw in enumerate(sessions):
        block = dict(raw)
        planned = number(block.get("hours"), "复习块小时数", 0, 1000)
        used = min(planned, spent_by_date[block["date"]]) if plan_id else 0
        spent_by_date[block["date"]] = max(0, spent_by_date[block["date"]] - used)
        if block.get("completed"):
            used = planned
        if used:
            completed.append({**block, "hours": round(used, 4), "completed": True, "scope": f"已投入：{block.get('scope', '')}"})
        remaining = planned - used
        if block.get("cancelled"):
            if remaining > 0.00001 or not used:
                completed.append({**block, "planned_hours": block.get("planned_hours", remaining), "hours": 0, "completed": False})
            continue
        if remaining <= 0.00001:
            continue
        ids = block.get("material_ids", [])
        material_ids.update(ids)
        if any(material_id not in materials for material_id in ids):
            raise AgentError(409, "复习计划引用的资料已被删除，请先修正计划")
        current_mastery = sum(states.get(material_id, {}).get("mastery", 0) for material_id in ids) / len(ids) if ids else block.get("mastery", 0)
        previous_mastery = number(block.get("mastery", 0), "掌握度", 0, 1)
        current_mastery = number(current_mastery, "当前掌握度", 0, 1)
        remaining *= (1.6 - 0.6 * current_mastery) / (1.6 - 0.6 * previous_mastery)
        block_course_id = block.get("course_id") or next((course["id"] for course in courses if course["name"] == block.get("course")), None)
        matching_tasks = [task for task in tasks if task["title"] == block.get("exam") and task["course_id"] == block_course_id]
        if not matching_tasks:
            matching_tasks = [task for task in tasks if task["course_id"] == block_course_id]
        deadlines = [datetime.fromisoformat(service._stored_time(task["due_at"])).astimezone(zone).date() for task in matching_tasks]
        deadline = min(deadlines) if deadlines else date.fromisoformat(block["exam_date"])
        if (deadline - today).days > 180:
            raise AgentError(422, "暂支持 180 天以内的复习计划")
        overdue = any(states.get(material_id, {}).get("due_at") and service._stored_time(states[material_id]["due_at"]) <= service.now() for material_id in ids)
        units.append({"block": block, "remaining": remaining, "deadline": deadline, "mastery": current_mastery, "priority": (deadline, not overdue, current_mastery, index)})
    units.sort(key=lambda unit: unit["priority"])
    daily_used = defaultdict(float)
    scheduled, backlog = [], []
    for unit in units:
        remaining = unit["remaining"]
        day = today
        while remaining > 0.00001 and day < unit["deadline"] and (not today_only or day == today):
            key = day.isoformat()
            # hours_per_day is a total daily time envelope, including classes and other plans.
            capacity = max(0, hours_per_day - _class_hours(courses, day) - budget_by_date[key] - other_planned[key] - daily_used[key])
            if day == today:
                now_local = service.clock().astimezone(zone)
                capacity = min(capacity, max(0, 24 - now_local.hour - now_local.minute / 60))
            duration = min(capacity, remaining)
            if duration > 0.00001:
                block = {**unit["block"], "date": key, "hours": round(duration, 4), "mastery": unit["mastery"], "exam_date": unit["deadline"].isoformat(), "completed": False}
                scheduled.append(block)
                daily_used[key] += duration
                remaining -= duration
            day += timedelta(days=1)
        if remaining > 0.00001:
            backlog.append({"scope": unit["block"].get("scope", ""), "exam": unit["block"].get("exam", ""), "hours": round(remaining, 4), "deadline": unit["deadline"].isoformat()})
    warnings = []
    if backlog:
        warnings.append(f"现有时间容量无法安排全部复习，尚缺 {sum(item['hours'] for item in backlog):.2f} 小时；请调整每日可用时间或计划范围")
    result = {"sessions": completed + sorted(scheduled, key=lambda block: block["date"]), "completed_hours": round(sum(block["hours"] for block in completed), 4), "scheduled_hours": round(sum(block["hours"] for block in scheduled), 4), "unallocated": backlog, "hours_per_day": hours_per_day, "daily_hours": dict(daily_used)}
    checks = [{"table": "tasks", "id": task["id"], "before": task} for task in tasks]
    checks += [{"table": "courses", "id": course["id"], "before": course} for course in courses]
    checks += [{"table": "materials", "id": row_id, "before": materials[row_id]} for row_id in material_ids]
    checks += other_checks
    dependencies = [{"kind": "progress", "id": plan_id or 0, "digest": service._dependency_digest(con, "progress", plan_id or 0)}]
    return result, warnings, checks, dependencies


def preview_replan(service, plan_id, *, hours_per_day=3, scope="local"):
    identifier(plan_id, "plan_id")
    number(hours_per_day, "每日可用小时数", 0.25, 16)
    with service.db.snapshot() as con:
        plan = service.db.row(con, "review_plans", plan_id)
        if not plan:
            raise AgentError(404, "复习计划不存在")
        tasks, finished = [], []
        for task_id in json.loads(plan["exam_task_ids"]):
            task = service.db.row(con, "tasks", task_id)
            if not task:
                raise AgentError(409, "计划关联考试已被删除")
            if not task["done"]:
                tasks.append(task)
            else:
                finished.append(task)
        courses = [dict(row) for row in con.execute("SELECT * FROM courses")]
        course_names = {course["id"]: course["name"] for course in courses}
        sessions = json.loads(plan["sessions"])
        for block in sessions:
            if any(task["title"] == block.get("exam") and course_names.get(task["course_id"]) == block.get("course") for task in finished):
                block["cancelled"] = True
        result, warnings, checks, dependencies = _proposal(service, con, tasks, sessions, courses, plan_id=plan_id, hours_per_day=hours_per_day)
        checks.extend({"table": "tasks", "id": task["id"], "before": task} for task in finished)
        if finished:
            warnings.append("已结束考试对应的复习块保留为历史，不再安排剩余复习")
        checks.append({"table": "review_plans", "id": plan_id, "before": plan})
        proposal = {"summary": f"保留已投入 {result['completed_hours']:.2f} 小时，重排剩余 {result['scheduled_hours']:.2f} 小时复习", "result": {**result, "review_plan_id": plan_id}, "warnings": warnings, "checks": checks, "dependencies": dependencies, "actions": [{"op": "update", "table": "review_plans", "id": plan_id, "values": {"sessions": canonical(result["sessions"]), "updated_at": service.now()}}]}
        if result["unallocated"]:
            proposal["blocked_reason"] = warnings[0]
    return service.save_plan("review_replan", proposal, scope=scope)


def preview_generate(service, exam_task_ids, *, hours_per_day=3, today_only=False, scope="local"):
    if not isinstance(today_only, bool):
        raise AgentError(422, "today_only 必须是布尔值")
    number(hours_per_day, "每日可用小时数", 0.25, 16)
    if not isinstance(exam_task_ids, list) or not 1 <= len(exam_task_ids) <= 30:
        raise AgentError(422, "请选择 1 到 30 个考试任务")
    for task_id in exam_task_ids:
        identifier(task_id, "exam_task_id")
    with service.db.snapshot() as con:
        tasks = []
        for task_id in dict.fromkeys(exam_task_ids):
            identifier(task_id, "exam_task_id")
            task = service.db.row(con, "tasks", task_id)
            if not task or task["done"]:
                raise AgentError(404, "考试任务不存在或已完成")
            if task["course_id"] is None:
                raise AgentError(422, "考试任务需先绑定课程")
            tasks.append(task)
        tasks.sort(key=lambda task: service._stored_time(task["due_at"]))
        courses = [dict(row) for row in con.execute("SELECT * FROM courses")]
        by_id = {row["id"]: row for row in courses}
        sessions, seen_courses = [], set()
        zone = ZoneInfo(service.timezone_name)
        for task in tasks:
            if task["course_id"] in seen_courses:
                continue
            seen_courses.add(task["course_id"])
            for material in con.execute("SELECT * FROM materials WHERE course_id=? ORDER BY exam_focus DESC,id", (task["course_id"],)):
                state = con.execute("SELECT mastery FROM material_reviews WHERE material_id=?", (material["id"],)).fetchone()
                mastery = state[0] if state else 0
                difficulty = {"easy": 0.8, "medium": 1, "hard": 1.5, "": 1}.get(material["difficulty"], 1)
                hours = max(0.5, min(3, len(material["text"]) / 6000)) * difficulty * (1.3 if material["exam_focus"] else 1) * (1.6 - 0.6 * mastery)
                sessions.append({"date": service.clock().astimezone(zone).date().isoformat(), "course_id": task["course_id"], "course": by_id[task["course_id"]]["name"], "scope": material["title"], "material_ids": [material["id"]], "materials": [material["title"]], "hours": hours, "mastery": mastery, "exam": task["title"], "exam_date": datetime.fromisoformat(service._stored_time(task["due_at"])).astimezone(zone).date().isoformat()})
        if not sessions:
            raise AgentError(422, "所选课程还没有可复习资料")
        result, warnings, checks, dependencies = _proposal(service, con, tasks, sessions, courses, plan_id=None, hours_per_day=hours_per_day, today_only=today_only)
        now = service.now()
        proposal = {"summary": f"为 {len(seen_courses)} 门课程安排 {result['scheduled_hours']:.2f} 小时复习", "result": result, "warnings": warnings, "checks": checks, "dependencies": dependencies, "actions": [{"op": "create", "table": "review_plans", "values": {"title": "Agent 复习计划", "exam_task_ids": canonical(exam_task_ids), "sessions": canonical(result["sessions"]), "created_at": now, "updated_at": now, "sync_uuid": str(uuid4())}}]}
        if result["unallocated"]:
            proposal["blocked_reason"] = warnings[0]
    return service.save_plan("review_generate", proposal, scope=scope)
