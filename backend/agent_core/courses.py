"""事务化课表导入：把已解析的课表条目换算成可预览、可执行、可撤销的建议（plan）。

为什么单独成模块：
- 校验与合并规则是纯计算，独立于事务核心，便于单元测试；api.py 只做参数分派；
- 请求体一律视为**不可信数据**：本模块只按固定字段名读取契约内的键，从不接受
  scope / 表名 / SQL / 任意列名，因此 actions 里的表名与列名只可能来自本模块常量。

与宿主保持一致：
- `courses.slots` 是 TEXT 列、存 JSON 数组字符串，写入必须用 canonical([...])，
  读取时 json.loads 得到 list（app/models.py 的 JSON 列、app/sync.py 原样同步）；
- 同名课程合并口径与 app/routers/courses.py:apply_course_import 对齐：名称按
  normalize()（NFKC + 去空白 + casefold）比较；同 (weekday,start,end) 的时段并集
  周次；code/teacher/location/semester/start_date 只补空、绝不覆盖用户已有值。
"""
from collections import defaultdict
from datetime import date
from uuid import NAMESPACE_URL, uuid5
import json
import re

from .common import AgentError, canonical, normalize, text

# 上限：一次请求 100 门课 / 每门 50 个时段 / weeks 取值 1..60（与宿主契约一致）
MAX_ENTRIES = 100
MAX_SLOTS_PER_ENTRY = 50
MAX_WEEKS = 60
# 单次课时不超过 12 小时；整批总时长上限 1000 小时——都远大于真实课表，
# 只为挡住畸形请求，避免预览接口被超大 payload 拖慢。
MAX_SLOT_MINUTES = 12 * 60
MAX_TOTAL_MINUTES = 1000 * 60
# 冲突检测（提示用）最多扫描的时段数，以及最多返回的冲突条数
MAX_SCAN_SLOTS = 4000
MAX_CONFLICTS = 50
DEFAULT_COLOR = "#6366f1"
TIME_PATTERN = re.compile(r"^([01][0-9]|2[0-3]):([0-5][0-9])$")
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
WEEKDAY_NAMES = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def _minutes(clock):
    """'HH:MM' → 当天分钟数（调用前已通过 _clock 校验）。"""
    return int(clock[:2]) * 60 + int(clock[3:])


def _format(total):
    return f"{total // 60:02d}:{total % 60:02d}"


def _clean(value, field, limit):
    """可选字符串字段：None 视为省略，其余一律过公共 text() 校验（非法类型直接 422）。"""
    return text("" if value is None else value, field, limit)


def _clock(value, field):
    """墙上时间只接受 HH:MM（课表语义，不做时区换算）。"""
    value = _clean(value, field, 5)
    if not TIME_PATTERN.fullmatch(value):
        raise AgentError(422, f"{field} 必须是 HH:MM 格式（如 08:00）")
    return value


def _weeks(value, field):
    """周次列表：1..60 的整数数组，升序去重；空 = 未注明。"""
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > MAX_WEEKS:
        raise AgentError(422, f"{field} 必须是 1 到 {MAX_WEEKS} 的整数数组")
    result = set()
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int) or not 1 <= item <= MAX_WEEKS:
            raise AgentError(422, f"{field} 的每个周次都必须在 1 到 {MAX_WEEKS} 之间")
        result.add(item)
    return sorted(result)


def _weeks_of(slot):
    """读取已有时段里的 weeks：历史数据可能有杂质，只取契约内的整数。"""
    value = slot.get("weeks")
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, int) and not isinstance(item, bool) and 1 <= item <= MAX_WEEKS]


def _existing_slots(row):
    """已有课程的 slots 列 → list[dict]；损坏的非对象项无法参与合并，直接忽略。"""
    try:
        value = json.loads(row.get("slots") or "[]")
    except (TypeError, ValueError):
        value = []
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _sort_key(slot):
    """时段排序键（weekday,start,end）：损坏字段降级为 0/""，避免排序抛 TypeError。

    排序让「同一批课表换个顺序再导入」不会产生无意义的写入，
    也让写进 slots 的 JSON 字符串稳定可比。
    """
    weekday = slot.get("weekday")
    return (
        weekday if isinstance(weekday, int) and not isinstance(weekday, bool) else 0,
        slot.get("start") if isinstance(slot.get("start"), str) else "",
        slot.get("end") if isinstance(slot.get("end"), str) else "",
    )


def _merge_slots(existing, incoming):
    """把新时段并入已有课表：同 (weekday,start,end) 并集周次，其余追加。

    返回 (merged, added, combined)：added 是真正新增的时段数，combined 是与
    已有相同时段合并周次的个数。location/teacher 只补空、不覆盖（与宿主
    app/courseimport.py:merge_slots 口径一致），保证重复导入是幂等的。
    """
    merged = [dict(slot) for slot in existing]
    added = combined = 0
    for slot in incoming:
        hit = next(
            (
                item
                for item in merged
                if item.get("weekday") == slot["weekday"]
                and item.get("start") == slot["start"]
                and item.get("end") == slot["end"]
            ),
            None,
        )
        if hit is None:
            merged.append(dict(slot))
            added += 1
            continue
        combined += 1
        weeks = sorted(set(_weeks_of(hit)) | set(slot["weeks"]))
        if weeks != hit.get("weeks"):
            hit["weeks"] = weeks
        if not hit.get("location") and slot["location"]:
            hit["location"] = slot["location"]
        if not hit.get("teacher") and slot["teacher"]:
            hit["teacher"] = slot["teacher"]
    return sorted(merged, key=_sort_key), added, combined


def _slot(raw, label):
    """单条时段：只读取契约内的固定字段，多余的键一律忽略。"""
    if not isinstance(raw, dict):
        raise AgentError(422, f"{label}必须是对象")
    weekday = raw.get("weekday")
    if isinstance(weekday, bool) or not isinstance(weekday, int) or not 1 <= weekday <= 7:
        raise AgentError(422, f"{label}的 weekday 必须是 1 到 7 的整数（1=周一 … 7=周日）")
    start = _clock(raw.get("start"), f"{label}的 start")
    end = _clock(raw.get("end"), f"{label}的 end")
    if _minutes(end) <= _minutes(start):
        raise AgentError(422, f"{label}的 end 必须晚于 start")
    if _minutes(end) - _minutes(start) > MAX_SLOT_MINUTES:
        raise AgentError(422, f"{label}的单次时长不能超过 12 小时")
    return {
        "weekday": weekday,
        "start": start,
        "end": end,
        "weeks": _weeks(raw.get("weeks"), f"{label}的 weeks"),
        "location": _clean(raw.get("location"), f"{label}的 location", 200),
        "teacher": _clean(raw.get("teacher"), f"{label}的 teacher", 120),
    }


def _entries(entries):
    """校验并归一化条目；同批同名课程先合并，避免同一次导入重复建课。"""
    if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_ENTRIES:
        raise AgentError(422, f"请提交 1 到 {MAX_ENTRIES} 门课程的课表条目")
    merged, total_minutes = {}, 0
    for index, raw in enumerate(entries):
        if not isinstance(raw, dict):
            raise AgentError(422, f"第 {index + 1} 门课程条目必须是对象")
        raw_name = raw.get("name")
        if not isinstance(raw_name, str) or not raw_name.strip():
            raise AgentError(422, f"第 {index + 1} 门课程缺少 name（课程名）")
        name = text(raw_name, f"第 {index + 1} 门课程的 name", 120, True)
        label = f"第 {index + 1} 门课程「{name}」"
        raw_slots = raw.get("slots", [])
        if not isinstance(raw_slots, list) or len(raw_slots) > MAX_SLOTS_PER_ENTRY:
            raise AgentError(422, f"{label}的 slots 必须是数组，最多 {MAX_SLOTS_PER_ENTRY} 个时段")
        slots = [_slot(item, f"{label}第 {position + 1} 个时段") for position, item in enumerate(raw_slots)]
        slots.sort(key=_sort_key)
        total_minutes += sum(_minutes(item["end"]) - _minutes(item["start"]) for item in slots)
        if total_minutes > MAX_TOTAL_MINUTES:
            raise AgentError(422, "单次导入的课表总时长过大，请分批导入")
        item = {
            "name": name,
            "code": _clean(raw.get("code"), f"{label}的 code", 40),
            "teacher": _clean(raw.get("teacher"), f"{label}的 teacher", 120),
            "location": _clean(raw.get("location"), f"{label}的 location", 200),
            "slots": slots,
        }
        current = merged.get(normalize(name))
        if current is None:
            merged[normalize(name)] = item
            continue
        current["code"] = current["code"] or item["code"]
        current["teacher"] = current["teacher"] or item["teacher"]
        current["location"] = current["location"] or item["location"]
        current["slots"], _, _ = _merge_slots(current["slots"], item["slots"])
    return list(merged.values())


def _anchor_date(value):
    """学期锚点：只接受 YYYY-MM-DD（与宿主 courses.start_date 列一致）。"""
    if value is None or value == "":
        return None
    value = text(value, "学期开始日期", 20)
    if not DATE_PATTERN.fullmatch(value):
        raise AgentError(422, "term_start_date 必须是 YYYY-MM-DD 格式")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise AgentError(422, "term_start_date 不是有效日期") from None
    if not 2000 <= parsed.year <= 2100:
        raise AgentError(422, "term_start_date 的年份需要在 2000 到 2100 之间")
    return value


def _conflicts(timetable):
    """同一 weekday 上时间重叠的时段对：只做提示，不阻塞执行。

    timetable 是 [(课程名, slots)]；重叠区间取交集（max(开始), min(结束)），
    这样前端能直接高亮真正的冲突时段，而不是把两个整段都报出来。
    """
    by_day = defaultdict(list)
    for name, slots in timetable:
        for slot in slots:
            if not isinstance(slot, dict):
                continue
            weekday, start, end = slot.get("weekday"), slot.get("start"), slot.get("end")
            if isinstance(weekday, bool) or not isinstance(weekday, int) or not 1 <= weekday <= 7:
                continue
            if not isinstance(start, str) or not isinstance(end, str):
                continue
            if not TIME_PATTERN.fullmatch(start) or not TIME_PATTERN.fullmatch(end):
                continue
            if _minutes(end) <= _minutes(start):
                continue
            by_day[weekday].append((_minutes(start), _minutes(end), name))
    found = []
    for weekday in sorted(by_day):
        active = None  # 当前重叠簇里结束最晚的时段 (结束分钟, 课程名)
        for start, end, name in sorted(by_day[weekday]):
            if active and start < active[0]:
                overlap = (start, min(end, active[0]))
                names = sorted({name, active[1]})
                day = WEEKDAY_NAMES[weekday - 1]
                window = f"{_format(overlap[0])}-{_format(overlap[1])}"
                detail = f"{day}「{names[0]}」自身时段重叠（{window}）" if len(names) == 1 else f"{day}「{names[0]}」与「{names[1]}」时间重叠（{window}）"
                found.append({"weekday": weekday, "start": _format(overlap[0]), "end": _format(overlap[1]), "courses": names, "message": detail})
                if len(found) >= MAX_CONFLICTS:
                    return found
            if active is None or end > active[0]:
                active = (end, name)
    return found


def preview_import(service, entries, term_start_date=None, semester="", scope="local"):
    """课表导入预览 → 建议（plan）。

    只读学习库、只写 agent_plans；真正的课程落库由统一执行器负责
    （POST /api/agent/plans/{plan_id}/apply），因此回执与撤销天然复用既有通道。
    """
    scope = service.scope(scope)
    semester = _clean(semester, "学期", 40)
    start_date = _anchor_date(term_start_date)
    wanted = _entries(entries)
    with service.db.snapshot() as con:
        rows = [dict(row) for row in con.execute("SELECT * FROM courses ORDER BY id")]
        by_name = {}
        for row in rows:
            by_name.setdefault(normalize(row["name"]), row)
        now, actions, checks, previews, warnings = service.now(), [], [], [], []
        merged_by_id, created, merged_summary, new_names = {}, [], [], []
        for item in wanted:
            name, incoming = item["name"], item["slots"]
            row = by_name.get(normalize(name))
            if row is None:
                # 新课程：values 必须给全 courses 表的 NOT NULL 列（含 slots 的 JSON 字符串）
                slots = [dict(slot) for slot in incoming]
                actions.append({
                    "table": "courses",
                    "op": "upsert",
                    "values": {
                        "name": name,
                        "code": item["code"],
                        "semester": semester,
                        "start_date": start_date,
                        "color": DEFAULT_COLOR,
                        "teacher": item["teacher"],
                        "location": item["location"],
                        "slots": canonical(slots),
                        # 同步标识按「范围 + 归一化课名」确定生成：同一门课在两台设备
                        # 各自导入会得到同一个 sync_uuid，多端合并不会各留一份重复课程。
                        "sync_uuid": str(uuid5(NAMESPACE_URL, canonical(["studyos-agent-courses", scope, normalize(name)]))),
                        "created_at": now,
                        "updated_at": now,
                    },
                })
                previews.append({
                    "name": name, "code": item["code"], "teacher": item["teacher"], "location": item["location"],
                    "slots": slots, "exists": False, "existing_id": None, "color": DEFAULT_COLOR,
                    "added_slots": len(slots), "merged_slots": 0, "changed": True, "current": None,
                })
                # 新建类动作的并发保护：预览时同名课程不存在，执行时必须**仍然**不存在。
                # 否则同一门课被两个预览都判为「新建」时，后执行的会插入重复课程
                # （宿主 courses.name 有唯一索引时更会直接约束冲突）。
                checks.append({"kind": "absent", "table": "courses", "name": name})
                new_names.append(name)
                created.append((name, slots))
                continue
            before = _existing_slots(row)
            slots, added, combined = _merge_slots(before, incoming)
            values = {
                field: value
                for field, value in (
                    ("code", item["code"]), ("teacher", item["teacher"]), ("location", item["location"]),
                    ("semester", semester), ("start_date", start_date),
                )
                if value and not row.get(field)
            }
            if slots != before:
                values["slots"] = canonical(slots)
            if values:
                # updated_at 由执行器统一改写为执行时刻；写它保证 changes 里能看出这次修改
                values["updated_at"] = now
                actions.append({"table": "courses", "op": "upsert", "id": row["id"], "values": values})
                # checks 用整行快照：生成建议后有人改过这门课就拒绝执行（409），
                # 避免把用户后来手改的课表覆盖掉。
                checks.append({"kind": "row", "table": "courses", "id": row["id"], "before": row})
            previews.append({
                # 条目部分回显「本次提交的内容」（与宿主 CourseImportEntryOut 同形），
                # current 给出合并前该课程的真实状态，方便前端展示「合并前 → 合并后」。
                "name": row["name"], "code": item["code"], "teacher": item["teacher"], "location": item["location"],
                "slots": [dict(slot) for slot in incoming], "exists": True, "existing_id": row["id"],
                "color": row.get("color") or DEFAULT_COLOR,
                "added_slots": added, "merged_slots": combined, "changed": bool(values),
                "current": {
                    "name": row["name"], "code": row.get("code", ""), "teacher": row.get("teacher", ""),
                    "location": row.get("location", ""), "semester": row.get("semester", ""),
                    "start_date": row.get("start_date"), "slots": before,
                },
            })
            merged_by_id[row["id"]] = (row["name"], slots)
            merged_summary.append({"id": row["id"], "name": row["name"], "added_slots": added, "merged_slots": combined, "changed": bool(values)})
        # 冲突检测看「导入后的完整课表」：未涉及的课程也要参与，才能发现新课与旧课撞车
        full = [[row["name"], _existing_slots(row)] for row in rows if row["id"] not in merged_by_id]
        full += [[name, slots] for name, slots in merged_by_id.values()]
        full += created
        if sum(len(slots) for _, slots in full) > MAX_SCAN_SLOTS:
            # 大课表下退化为只检测本次导入范围内，保证预览响应时间可控
            scan = [[name, slots] for name, slots in merged_by_id.values()] + created
            warnings.append("现有课表时段较多，本次只检测导入范围内的时段冲突")
        else:
            scan = full
        conflicts = _conflicts(scan)
        if conflicts:
            warnings.append(f"检测到 {len(conflicts)} 处时间冲突，请核对课表")
        slot_count = sum(len(item["slots"]) for item in wanted)
        if actions:
            summary = f"导入 {len(wanted)} 门课程：新建 {len(new_names)} 门、合并 {len(merged_summary)} 门（共 {slot_count} 个时段）"
        else:
            summary = f"{len(wanted)} 门课程与现有课表一致，无需写入"
        proposal = {
            "summary": summary,
            "result": {
                "new_courses": new_names,
                "merged_courses": merged_summary,
                "slot_count": slot_count,
                "conflict_count": len(conflicts),
            },
            "entries": previews,
            "conflicts": conflicts,
            "warnings": warnings,
            "checks": checks,
            "actions": actions,
        }
    return service.save_plan("course_import", proposal, scope=scope)
