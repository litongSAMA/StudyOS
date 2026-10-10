"""间隔重复调度引擎（SM-2 变体 + 连续掌握程度）。

设计要点：
- 评分四档：again（忘了）/ hard（吃力）/ good（正常）/ easy（轻松），
  映射 SM-2 质量分 1 / 3 / 4 / 5；
- 间隔阶梯（面向备考节奏，比经典 SM-2 的 1/6 天起步更密集）：
  1 → 2 → 4 → 7 → 14 → 30 天，hard/easy 再乘 0.7 / 1.3 微调；
- 难度系数 ease 按 SM-2 公式更新（下限 1.3），again 视为遗忘：
  连续成功次数清零、间隔回到 1 天、ease 减 0.2；
- mastery 是 0~1 的连续掌握程度：评分决定增减、clamp 到 [0, 1]，
  复习计划按 (1 - mastery) 加权分配学习时长。

纯函数实现（不碰数据库）：单元测试与路由层共用同一套公式。
"""
from __future__ import annotations

from datetime import datetime, timedelta

# 评分 → SM-2 质量分
RATING_QUALITY = {"again": 1, "hard": 3, "good": 4, "easy": 5}

# 连续成功复习次数对应的间隔天数（备考导向：1/2/4/7/14/30 天阶梯）
INTERVALS = (1, 2, 4, 7, 14, 30)

# 评分对间隔的乘数：hard 提前一点、easy 推后一点
INTERVAL_FACTOR = {"again": 1.0, "hard": 0.7, "good": 1.0, "easy": 1.3}

# 评分对掌握程度的增减（clamp 到 [0, 1]）
MASTERY_DELTA = {"again": -0.35, "hard": 0.05, "good": 0.15, "easy": 0.25}

EASE_INIT = 2.5
EASE_MIN = 1.3
EASE_LAPSE_PENALTY = 0.2

# mastery ≥ 该值视为「已掌握」：计划里不再安排初次学习，只按间隔复习维持
MASTERED = 0.85

# 每次间隔复习（到期复习 / 计划检查点）的固定时长（小时）——检索练习比初学快
REVIEW_HOURS = 0.5

# 计划内预排的间隔复习检查点：首次学习后第 1 / 3 / 7 天（1+2、1+2+4 累积阶梯）
PROJECTED_OFFSETS = (1, 3, 7)

# 单个复习块每天最多安排的间隔复习资料数（避免一天塞爆，超出顺延/舍弃）
REVIEWS_PER_DAY = 6

# ---- 资料学习时长公式（复习计划与进度预测共用同一套口径）----
HOURS_BY_MATERIAL = 1.0          # 每份资料基础学习时长（小时）
DIFFICULTY_WEIGHT = {"": 1.0, "easy": 0.8, "medium": 1.0, "hard": 1.5}
EXAM_FOCUS_WEIGHT = 1.3          # 考试重点加权：老师强调的考点多花 30% 时间


def clamp01(v: float) -> float:
    return max(0.0, min(1.0, v))


def next_ease(ease: float, rating: str) -> float:
    """SM-2 难度系数更新：again 减 0.2；hard/good/easy 按质量分微调（下限 1.3）。"""
    if rating == "again":
        return max(EASE_MIN, ease - EASE_LAPSE_PENALTY)
    q = RATING_QUALITY[rating]
    return max(EASE_MIN, ease + (0.1 - (5 - q) * (0.08 + (5 - q) * 0.02)))


def next_interval(repetitions_after: int, rating: str, ease: float = EASE_INIT) -> float:
    """下一次复习间隔（天）：成功次数走 1/2/4/7/14/30 阶梯 × hard/easy 微调；
    again 一律回 1 天。保留 1 位小数。

    repetitions_after 是本次评分**之后**的连续成功次数：第 1 次成功 → 1 天，
    第 2 次 → 2 天，…（阶梯 0 号位起）。
    """
    if rating == "again":
        return 1.0
    step = INTERVALS[min(max(repetitions_after - 1, 0), len(INTERVALS) - 1)]
    return max(1.0, round(step * INTERVAL_FACTOR.get(rating, 1.0), 1))


def apply_rating(
    mastery: float,
    repetitions: int,
    lapses: int,
    ease: float,
    rating: str,
    now: datetime | None = None,
) -> dict:
    """把一次评分应用到复习状态，返回更新后的字段。

    rating ∈ {again, hard, good, easy}；now 用于计算下次到期时刻
    （naive UTC，间隔按天加）。
    """
    if rating not in RATING_QUALITY:
        raise ValueError(f"非法评分: {rating}")
    now = now or datetime.now()
    new_mastery = clamp01((mastery or 0.0) + MASTERY_DELTA[rating])
    new_ease = next_ease(ease or EASE_INIT, rating)
    if rating == "again":
        new_reps, new_lapses = 0, (lapses or 0) + 1
    else:
        new_reps, new_lapses = (repetitions or 0) + 1, lapses or 0
    interval = next_interval(new_reps, rating, new_ease)
    return {
        "mastery": new_mastery,
        "repetitions": new_reps,
        "lapses": new_lapses,
        "ease": new_ease,
        "interval_days": interval,
        "due_at": now + timedelta(days=interval),
        "last_rating": rating,
    }


def mastery_label(mastery: float, has_state: bool = True) -> str:
    """掌握程度 → 人话标签（供界面与复习范围文案）。"""
    if not has_state or (mastery or 0.0) <= 0.0:
        return "未复习"
    if mastery < 0.4:
        return "初学"
    if mastery < MASTERED:
        return "熟练"
    return "已掌握"


def study_weight(mastery: float) -> float:
    """初学时长加权：未掌握 1.6×、完全掌握 1.0×（线性插值）。

    复习计划的每份资料总学习时长 = 基础时长 × 难度权重 × 考点加权 × 此系数，
    掌握程度越高、需要的新学时间越少（维持交给间隔复习）。
    """
    return 1.6 - 0.6 * clamp01(mastery or 0.0)


def material_hours(material, mastery: float = 0.0) -> float:
    """单份资料的学习时长（小时）：基础 × 难度 × 考试重点 × 掌握系数。

    复习计划生成与进度预测共用此公式，保证「计划排了多少、预测算了多少」口径一致。
    `material` 只需具备 difficulty / exam_focus 两个字段（ORM 对象或 dict 均可）。
    """
    if isinstance(material, dict):
        difficulty, exam_focus = material.get("difficulty", ""), material.get("exam_focus", False)
    else:
        difficulty, exam_focus = getattr(material, "difficulty", ""), getattr(material, "exam_focus", False)
    w = DIFFICULTY_WEIGHT.get(difficulty or "", 1.0)
    if exam_focus:
        w *= EXAM_FOCUS_WEIGHT
    return HOURS_BY_MATERIAL * w * study_weight(mastery or 0.0)
