from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import json
import re

from .common import AgentError, identifier, normalize, text, utc

WEEKDAYS = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
NUMBERS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
CHANGE_MARKER = re.compile(r"延期|推迟|延后|改到|改为|调整到|提前到|延长至|截止时间.*(?:修改|调整)")


def chinese_number(value):
    if value.isdigit():
        return int(value)
    if "十" in value:
        left, right = value.split("十", 1)
        return NUMBERS.get(left, 1) * 10 + NUMBERS.get(right, 0)
    return NUMBERS.get(value)


def parse_deadline(message, *, now, zone, fallback_time=None):
    marker = CHANGE_MARKER.search(message)
    if marker:
        message = message[marker.end():]
    if re.search(r"待定|另行|暂未|可能|或者|或", message):
        raise AgentError(422, "新截止时间存在不确定表达，请补充明确日期和时刻")
    explicit_iso = re.search(r"20\d{2}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})", message)
    if explicit_iso:
        return utc(explicit_iso[0]), False
    local_now = now.astimezone(zone)
    day = None
    full = re.search(r"(20\d{2})[年/.-](\d{1,2})[月/.-](\d{1,2})(?:日|号)?", message)
    month_day = re.search(r"(?<!\d)(\d{1,2})月(\d{1,2})(?:日|号)", message)
    week = re.search(r"(下下|下|本|这)周(?:星期)?([一二三四五六日天])", message)
    relative = re.search(r"([\d零一二两三四五六七八九十]+)天后", message)
    try:
        if full:
            day = local_now.date().replace(year=int(full[1]), month=int(full[2]), day=int(full[3]))
        elif month_day:
            day = local_now.date().replace(month=int(month_day[1]), day=int(month_day[2]))
        elif week:
            weeks = {"下下": 2, "下": 1, "本": 0, "这": 0}[week[1]]
            day = local_now.date() + timedelta(days=-local_now.weekday() + weeks * 7 + WEEKDAYS[week[2]])
        elif "大后天" in message:
            day = local_now.date() + timedelta(days=3)
        elif "后天" in message:
            day = local_now.date() + timedelta(days=2)
        elif "明天" in message:
            day = local_now.date() + timedelta(days=1)
        elif "今天" in message or "今晚" in message:
            day = local_now.date()
        elif relative:
            delta = chinese_number(relative[1])
            if delta is None or not 0 <= delta <= 365:
                raise ValueError("bad relative date")
            day = local_now.date() + timedelta(days=delta)
    except ValueError:
        raise AgentError(422, "通知中的日期无效") from None
    if day is None:
        raise AgentError(422, "无法确定新截止日期，请补充明确日期")
    clock = re.search(r"(上午|下午|晚上|中午|凌晨|早上)?\s*(?<!\d)(\d{1,2})[:：](\d{2})(?!\d)", message)
    chinese_clock = re.search(r"(上午|下午|晚上|中午|凌晨|早上)?\s*([\d零一二两三四五六七八九十]+)[点时](半|[\d零一二两三四五六七八九十]+分)?", message)
    inherited = False
    if clock:
        hour, minute = int(clock[2]), int(clock[3])
        if clock[1] in {"下午", "晚上"} and 1 <= hour < 12:
            hour += 12
        elif clock[1] in {"凌晨", "早上"} and hour == 12:
            hour = 0
    elif chinese_clock:
        hour = chinese_number(chinese_clock[2])
        part = chinese_clock[3]
        minute = 30 if part == "半" else (chinese_number(part[:-1]) if part else 0)
        period = chinese_clock[1]
        if hour is not None and period in {"下午", "晚上"} and 1 <= hour < 12:
            hour += 12
        elif hour == 12 and period in {"凌晨", "早上"}:
            hour = 0
    elif fallback_time is not None:
        hour, minute, inherited = fallback_time.hour, fallback_time.minute, True
    else:
        raise AgentError(422, "通知未说明具体时刻，请补充截止时间")
    if hour is None or minute is None or not 0 <= hour <= 24 or not 0 <= minute <= 59 or (hour == 24 and minute != 0):
        raise AgentError(422, "通知中的时刻无效")
    if hour == 24:
        day += timedelta(days=1)
        hour = 0
    local = datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone)
    if local.astimezone(ZoneInfo("UTC")).astimezone(zone).replace(tzinfo=None) != local.replace(tzinfo=None):
        raise AgentError(422, "此本地时刻因夏令时变化不存在")
    if local.replace(fold=0).utcoffset() != local.replace(fold=1).utcoffset():
        raise AgentError(422, "此本地时刻出现两次，请指定带时区偏移的确切时间")
    return utc(local.isoformat()), inherited


def preview_deadline(service, message, *, task_id=None, new_due_at=None, course=None, scope="local"):
    message = text(message, "延期通知", 10000, True)
    if course is not None:
        course = text(course, "课程范围", 120)
    if not CHANGE_MARKER.search(message):
        raise AgentError(422, "通知未包含延期或截止日期变更信息")
    with service.db.snapshot() as con:
        candidates = []
        for row in con.execute("SELECT tasks.*,courses.name AS course_name FROM tasks LEFT JOIN courses ON courses.id=tasks.course_id WHERE tasks.done=0 ORDER BY tasks.due_at,tasks.id"):
            row = dict(row)
            if course and normalize(row["course_name"]) != normalize(course):
                continue
            course_matches = normalize(row["course_name"]) and normalize(row["course_name"]) in normalize(message)
            title_matches = normalize(row["title"]) and normalize(row["title"]) in normalize(message)
            if title_matches or (course_matches and any(word in message and word in row["title"] for word in ["报告", "作业", "实验", "考试", "答辩"])):
                candidates.append({"id": row["id"], "title": row["title"], "course": row["course_name"], "due_at": service._stored_time(row["due_at"])})
        if task_id is None:
            if len(candidates) != 1:
                return {"intent": "deadline_change", "needs_selection": True, "candidates": candidates, "message": "请选择需要更新的原任务" if candidates else "没有匹配到原任务，请指定任务后重试"}
            task_id = candidates[0]["id"]
        identifier(task_id, "task_id")
        before = service.db.row(con, "tasks", task_id)
        if not before:
            raise AgentError(404, "原任务不存在")
        if before["done"]:
            raise AgentError(409, "已完成任务不能自动调整截止日期")
        if course:
            matched_course = con.execute("SELECT name FROM courses WHERE id=?", (before["course_id"],)).fetchone()
            if not matched_course or normalize(matched_course[0]) != normalize(course):
                raise AgentError(409, "选择的任务不属于指定课程")
        old_due = service._stored_time(before["due_at"])
        inherited = False
        if new_due_at:
            new_due = utc(new_due_at)
        else:
            zone = ZoneInfo(service.timezone_name)
            new_due, inherited = parse_deadline(message, now=service.clock(), zone=zone, fallback_time=datetime.fromisoformat(old_due).astimezone(zone))
        if datetime.fromisoformat(new_due) <= service.clock():
            raise AgentError(422, "新截止时间已过去，请重新核对")
        if re.search(r"延期|推迟|延后|延长", message) and new_due <= old_due:
            raise AgentError(422, "延期后的截止时间必须晚于原时间")
        deliveries = [dict(row) for row in con.execute("SELECT * FROM push_deliveries WHERE task_id=? ORDER BY id", (task_id,))]
        future_reminders = [offset for offset in json.loads(before["remind_offsets_h"]) if datetime.fromisoformat(new_due) - timedelta(hours=offset) > service.clock()]
        proposal = {
            "summary": f"更新「{before['title']}」截止时间并重置提醒记录",
            "checks": [{"table": "tasks", "id": task_id, "before": before}],
            "dependencies": [{"kind": "deliveries", "id": task_id, "digest": service._dependency_digest(con, "deliveries", task_id)}],
            "actions": [*[{"op": "delete", "table": "push_deliveries", "id": row["id"]} for row in deliveries], {"op": "update", "table": "tasks", "id": task_id, "values": {"due_at": new_due, "updated_at": service.now()}}],
            "result": {"task_id": task_id, "title": before["title"], "old_due_at": old_due, "new_due_at": new_due, "inherited_time": inherited, "remind_offsets_h": json.loads(before["remind_offsets_h"]), "future_remind_offsets_h": future_reminders},
            "warnings": ["通知未说明时刻，沿用原任务的本地截止时刻，请核对"] if inherited else [],
        }
    return service.save_plan("deadline_change", proposal, scope=scope)
