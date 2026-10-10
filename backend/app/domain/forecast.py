"""学习进度预测 + 负载预警（纯函数引擎，不碰数据库）。

与复习计划共用同一套资料时长公式（spacedrep.material_hours），保证
「计划排了多少、预测算了多少」口径一致。三个输出：

1. **课程进度预测**：对每场未完成的考试/大作业，按 DDL 倒排计算
   - required_pace = 剩余学时 / 剩余天数，与每日学习容量对比给出状态：
     on_track（≤80% 容量）/ tight（≤容量）/ at_risk（>容量或已过期）；
   - 顺序仿真：每天把容量先分给最早到期的课程（剩余顺延下一门），
     得到每门课的预计完成日期与考试日预计掌握程度；
2. **负载预警**：未来 N 天逐日汇总「DDL 截止权重 + 复习计划学时 +
   到期间隔复习学时」，超过每日容量 100% / 150% 分别预警 busy / overload，
   并追加「连续高负载」「考试撞车」两类结构化告警；
3. **进度曲线**：同一仿真逐日输出预计总掌握程度（按资料数加权），
   供前端画「预计掌握度曲线」。

入口 `compute_forecast(...)` 接收普通 dict/list（date 用 datetime.date），
单元测试与路由层共用同一套公式。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from app.domain.spacedrep import MASTERED, REVIEW_HOURS, material_hours
from app.infra.timeutil import utcnow

# 不同任务类型对当日负载的权重（考试/大作业比普通作业更重）
KIND_LOAD_WEIGHT = {
    "exam": 2.0,
    "report": 1.5,
    "lab": 1.2,
    "homework": 1.0,
    "review": 0.8,
    "other": 0.8,
}

ON_TRACK_RATIO = 0.8   # required_pace ≤ 80% 容量 → 进度良好
BUSY_RATIO = 1.0       # 日负载 > 容量 → busy
OVERLOAD_RATIO = 1.5   # 日负载 > 1.5× 容量 → overload
DEADLINE_BUSY = 3      # 单日 DDL 截止 ≥ 3 个 → busy
DEADLINE_OVERLOAD = 4  # 单日 DDL 截止 ≥ 4 个 → overload
CLUSTER_DAYS = 3       # 连续 N 天高负载 → 追加告警
EXAM_COLLISION_DAYS = 3  # 两场考试间隔不足 N 天 → 考试撞车告警
OVERDUE_EXAM_GRACE = 7   # 已过期的考试仍纳入预测的宽限天数（提醒补学）

STATUS_LABELS = {
    "idle": "待配置",
    "done": "已掌握",
    "on_track": "进度良好",
    "tight": "进度偏紧",
    "at_risk": "进度预警",
}
STATUS_RANK = {"done": 0, "on_track": 1, "tight": 2, "at_risk": 3}

_EPS = 1e-6


# ---------------------------------------------------------------------------
# 课程进度预测
# ---------------------------------------------------------------------------


def _course_metrics(course: dict, capacity: float, today: date) -> dict:
    """单门课的剩余学时 / 所需节奏 / 状态（预计完成与考试日掌握度由仿真补）。"""
    mats = course["materials"]
    masteries = [m["mastery"] for m in mats]
    current_mastery = sum(masteries) / len(masteries) if masteries else 0.0
    remaining_hours = sum(
        material_hours(m, m["mastery"]) for m in mats if m["mastery"] < MASTERED
    )
    total_hours = max(sum(material_hours(m, m["mastery"]) for m in mats), 2.0) if mats else 0.0
    days_left = (course["due_date"] - today).days

    out = {
        "current_mastery": current_mastery,
        "total_hours": total_hours,
        "remaining_hours": remaining_hours,
        "progress_pct": round(
            max(0.0, min(1.0, 1.0 - remaining_hours / total_hours)) * 100, 1
        ) if total_hours > _EPS else 100.0,
        "materials_total": len(mats),
        "materials_studied": sum(1 for m in mats if m["mastery"] > _EPS),
        "materials_mastered": sum(1 for m in mats if m["mastery"] >= MASTERED),
        "days_left": days_left,
    }
    if remaining_hours <= _EPS:
        out.update(status="done", required_pace_h=0.0)
        out["message"] = (
            "全部资料已掌握，只需按间隔复习维持"
            if mats else "该课程暂无资料，先去资料库补充"
        )
    elif days_left <= 0:
        out.update(status="at_risk", required_pace_h=round(remaining_hours, 1))
        out["message"] = "已到考试日仍未学完（或已逾期），风险极高"
    else:
        required = remaining_hours / days_left
        out["required_pace_h"] = round(required, 2)
        if required <= capacity * ON_TRACK_RATIO:
            out.update(status="on_track", message="按当前节奏可在考试前轻松完成")
        elif required <= capacity:
            out.update(status="tight", message="进度偏紧：需要接近满负荷学习")
        else:
            out.update(status="at_risk", message="按当前节奏预计无法在考试前学完")
    return out


def _simulate(courses: list[dict], capacity: float, today: date) -> tuple[list[dict], dict[int, date | None]]:
    """顺序仿真：每天把容量先分给最早到期的未完成课程，剩余容量顺延下一门。

    返回 (逐日曲线点, 课程 → 预计完成日期)。考试日已过的课程不再分配
    （补学对那场考试无意义），其预计完成日期为 None（前端显示「无法按时完成」）。
    """
    if not courses:
        return [], {}, {}

    ordered = sorted(range(len(courses)), key=lambda i: courses[i]["due_date"])
    remaining = {i: courses[i]["remaining_hours"] for i in ordered}
    done_by_due: dict[int, float] = {i: 0.0 for i in ordered}
    finish: dict[int, date | None] = {i: None for i in ordered}
    n_mats = [max(len(c["materials"]), 1) for c in courses]
    start_rem = dict(remaining)

    max_due = max(c["due_date"] for c in courses)
    # 最坏情况：全部剩余学时按容量逐日消化，再多留 7 天缓冲
    horizon = max_due + timedelta(days=int(sum(remaining.values()) / max(capacity, 0.5)) + 7)

    curve: list[dict] = []
    day = today
    total_rem = sum(remaining.values())
    while any(v > _EPS for v in remaining.values()) and day <= horizon:
        budget = capacity
        for i in ordered:
            if remaining[i] <= _EPS or courses[i]["due_date"] < day:
                continue  # 该课已学完，或考试日已过（不再分配）
            take = min(budget, remaining[i])
            if take <= _EPS:
                continue
            remaining[i] -= take
            budget -= take
            if remaining[i] <= _EPS:
                finish[i] = day
            if budget <= _EPS:
                break
        # 记录每门课「考试日为止」已消化的学时（掌握程度按它折算）
        for i in ordered:
            if day <= courses[i]["due_date"]:
                done_by_due[i] = start_rem[i] - remaining[i]
        if day <= max_due:
            # 当天各课预计掌握度 = 当前掌握 + 剩余比例折算，按资料数加权成总体曲线
            per_course = [
                c["current_mastery"] + (1.0 - c["current_mastery"]) * (
                    (start_rem[i] - remaining[i]) / start_rem[i] if start_rem[i] > _EPS else 1.0
                )
                for i, c in enumerate(courses)
            ]
            mastery = sum(p * w for p, w in zip(per_course, n_mats)) / sum(n_mats)
            curve.append({
                "date": day.isoformat(),
                "mastery": round(max(0.0, min(1.0, mastery)), 3),
                "hours_done": round(total_rem - sum(remaining.values()), 1),
                "hours_remaining": round(sum(remaining.values()), 1),
            })
        day += timedelta(days=1)

    predicted_mastery = {}
    for i, c in enumerate(courses):
        ratio = (done_by_due[i] / start_rem[i]) if start_rem[i] > _EPS else 1.0
        predicted_mastery[i] = round(
            max(0.0, min(1.0, c["current_mastery"] + (1.0 - c["current_mastery"]) * ratio)), 2
        )
    return curve, finish, predicted_mastery


# ---------------------------------------------------------------------------
# 负载预警
# ---------------------------------------------------------------------------


def _load_series(
    deadlines: list[tuple[date, str]],
    plan_sessions: list[tuple[date, float]],
    due_review_dates: list[date],
    capacity: float,
    window_days: int,
    today: date,
) -> tuple[list[dict], list[dict]]:
    """未来 N 天逐日负载：DDL 权重 + 计划学时 + 到期复习学时 → ok/busy/overload。"""
    plan_by_day: dict[date, float] = {}
    for d, h in plan_sessions:
        if d >= today:
            plan_by_day[d] = plan_by_day.get(d, 0.0) + h

    review_by_day: dict[date, int] = {}
    for d in due_review_dates:
        key = d if d >= today else today  # 逾期未复习 → 算作今天就要补
        review_by_day[key] = review_by_day.get(key, 0) + 1

    deadline_by_day: dict[date, float] = {}
    deadline_count: dict[date, int] = {}
    for d, kind in deadlines:
        key = d if d >= today else today
        w = KIND_LOAD_WEIGHT.get(kind, 0.8)
        deadline_by_day[key] = deadline_by_day.get(key, 0.0) + w
        deadline_count[key] = deadline_count.get(key, 0) + 1

    days: list[dict] = []
    warnings: list[dict] = []
    for off in range(window_days):
        d = today + timedelta(days=off)
        plan_h = round(plan_by_day.get(d, 0.0), 1)
        n_review = review_by_day.get(d, 0)
        review_h = round(REVIEW_HOURS * n_review, 1)
        n_ddl = deadline_count.get(d, 0)
        ddl_w = round(deadline_by_day.get(d, 0.0), 1)
        total_h = round(plan_h + review_h, 1)

        if total_h > capacity * OVERLOAD_RATIO or ddl_w >= DEADLINE_OVERLOAD:
            level = "overload"
        elif total_h > capacity * BUSY_RATIO or ddl_w >= DEADLINE_BUSY:
            level = "busy"
        else:
            level = "ok"

        days.append({
            "date": d.isoformat(),
            "deadline_count": n_ddl,
            "deadline_weight": ddl_w,
            "plan_hours": plan_h,
            "review_count": n_review,
            "review_hours": review_h,
            "total_hours": total_h,
            "level": level,
            "is_today": d == today,
        })

        if level == "overload":
            parts = []
            if total_h > capacity * OVERLOAD_RATIO:
                parts.append(f"学习负载 {total_h}h，超过每日容量 {capacity:.0f}h 的 150%")
            if n_ddl >= DEADLINE_OVERLOAD:
                parts.append(f"{n_ddl} 个 DDL 同日截止")
            warnings.append({
                "date": d.isoformat(), "level": level,
                "message": " · ".join(parts) + "，建议提前分摊或提高每日学习时长",
            })
        elif level == "busy":
            parts = []
            if total_h > capacity * BUSY_RATIO:
                parts.append(f"学习负载 {total_h}h，接近/超过每日容量 {capacity:.0f}h")
            if n_ddl >= DEADLINE_BUSY:
                parts.append(f"{n_ddl} 个 DDL 同日截止")
            warnings.append({
                "date": d.isoformat(), "level": level,
                "message": " · ".join(parts) + "，注意留出缓冲",
            })
    return days, warnings


def _extra_warnings(exams: list[dict], load_days: list[dict], today: date) -> list[dict]:
    """连续高负载 + 考试撞车 两类结构化告警。"""
    warnings: list[dict] = []

    # 连续 N 天 busy/overload → 单独提醒提前开始
    run_start = None
    for i, d in enumerate(load_days + [{"level": "ok", "date": ""}]):
        high = d["level"] in ("busy", "overload")
        if high and run_start is None:
            run_start = i
        elif not high and run_start is not None:
            if i - run_start >= CLUSTER_DAYS:
                warnings.append({
                    "date": load_days[run_start]["date"], "level": "overload",
                    "message": (
                        f"连续 {i - run_start} 天高负载（{load_days[run_start]['date'][5:]} ~ "
                        f"{load_days[i - 1]['date'][5:]}），建议提前开始复习或提高每日学习时长"
                    ),
                })
            run_start = None

    # 两场考试间隔不足 N 天 → 考试撞车
    ordered = sorted(exams, key=lambda e: e["due_date"])
    for a, b in zip(ordered, ordered[1:]):
        gap = (b["due_date"] - a["due_date"]).days
        if gap < EXAM_COLLISION_DAYS:
            warnings.append({
                "date": a["due_date"].isoformat(), "level": "busy",
                "message": (
                    f"考试撞车：「{a['exam_title']}」与「{b['exam_title']}」仅相隔 "
                    f"{max(gap, 0)} 天，两门课需并行复习"
                ),
            })
    return warnings


# ---------------------------------------------------------------------------
# 总入口
# ---------------------------------------------------------------------------


def compute_forecast(
    exams: list[dict],
    plan_sessions: list[tuple[date, float]],
    due_review_dates: list[date],
    deadlines: list[tuple[date, str]],
    hours_per_day: float,
    window_days: int,
    today: date | None = None,
    now: datetime | None = None,
    recent_review_dates: list[datetime] | None = None,
) -> dict:
    """计算完整预测结果（见模块 docstring）。

    exams: [{task_id, course_id, course_name, exam_title, due_date,
             materials: [{difficulty, exam_focus, mastery}]}]（按 DDL 时间不限序）
    其余参数见 _load_series / _simulate。
    """
    today = today or date.today()
    capacity = max(0.5, min(hours_per_day, 12.0))
    window = max(7, min(window_days, 60))

    courses = [
        {**e, **_course_metrics(e, capacity, today)}
        for e in exams
    ]

    # 顺序仿真 → 预计完成日期 / 考试日掌握度 / 进度曲线
    curve, finish, predicted_mastery = _simulate(courses, capacity, today)
    for i, c in enumerate(courses):
        fin = finish.get(i)
        c["predicted_finish_date"] = (
            fin.isoformat() if fin else (today.isoformat() if c["status"] == "done" else None)
        )
        c["predicted_mastery"] = predicted_mastery[i]
        # 仿真兜底：课程间会互相挤占容量——即使单课节奏够，
        # 顺序仿真仍完不成（或无完成日）时，状态升级为预警
        if c["status"] != "done" and (fin is None or fin >= c["due_date"]):
            c["status"] = "at_risk"
            c["message"] = "按当前节奏预计无法在考试前学完，建议提前开始或提高每日时长"

    # 负载 + 告警
    load_days, warnings = _load_series(
        deadlines, plan_sessions, due_review_dates, capacity, window, today
    )
    warnings += _extra_warnings(courses, load_days, today)
    # 告警按等级（overload 在前）→ 日期排序
    warnings.sort(key=lambda w: (0 if w["level"] == "overload" else 1, w["date"]))

    # 近 7 天实际复习节奏（按间隔复习评分记录近似；now 与 DB 同口径 naive UTC）
    recent = recent_review_dates or []
    now = now or utcnow()
    reviews_7d = sum(1 for dt in recent if dt and (now - dt).days < 7)
    required_pace = max((c["required_pace_h"] for c in courses), default=0.0)

    n_at_risk = sum(1 for c in courses if c["status"] == "at_risk")
    n_tight = sum(1 for c in courses if c["status"] == "tight")
    busy_days = sum(1 for d in load_days if d["level"] != "ok")

    if not courses:
        overall = {
            "status": "idle", "label": STATUS_LABELS["idle"],
            "message": "暂无未完成的考试/大作业，去「DDL 任务」添加后即可预测",
        }
    else:
        worst = max(STATUS_RANK[c["status"]] for c in courses)
        status = {v: k for k, v in STATUS_RANK.items()}[worst]
        overall = {"status": status, "label": STATUS_LABELS[status]}
        if status == "at_risk":
            overall["message"] = (
                f"{n_at_risk} 门课程预计无法在考试前学完，建议提前开始或提高每日学习时长"
            )
        elif status == "tight":
            overall["message"] = f"进度偏紧：{n_tight} 门课程需接近满负荷学习，注意负载预警"
        else:
            overall["message"] = "按当前节奏可完成全部复习，保持即可"

    return {
        "overall": {
            **overall,
            "courses_total": len(courses),
            "courses_at_risk": n_at_risk,
            "remaining_hours": round(sum(c["remaining_hours"] for c in courses), 1),
            "busy_days": busy_days,
        },
        "courses": courses,
        "load_days": load_days,
        "warnings": warnings,
        "curve": curve,
        "pace": {
            "reviews_last_7d": reviews_7d,
            "study_hours_last_7d": round(REVIEW_HOURS * reviews_7d, 1),
            "required_hours_per_day": round(required_pace, 2),
        },
    }
