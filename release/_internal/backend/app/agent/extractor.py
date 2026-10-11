"""抽取 Agent：规则引擎 + LLM 双通道，合并去重，置信度标注。

输入：文本 / 文件名 / 文件全文（截图经 OCR 后同样走这里）。
输出：ExtractResult（任务 + 资料建议 + 告警），不产生任何副作用——
确认后再由 apply 接口落库，保证「错了也不脏数据」。
"""
from __future__ import annotations

import re
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import AppSetting, Course
from ..schemas import ExtractResult, MaterialSuggestion, TaskSuggestion
from ..timeutil import localize
from . import llm
from .common import (
    classify_difficulty,
    classify_material_type,
    extract_chapter,
    extract_tags,
    has_exam_focus,
    match_course,
    normalize_difficulty,
)
from .timeparse import parse_single, parse_time_expr

DDL_HINTS = (
    "截止", "ddl", "deadline", "due", "提交", "上交", "交作业", "交报告",
    "考试", "测验", "答辩", "汇报", "验收", "检查", "presentation", "quiz",
)
# 非 DDL 关键词句，但出现时间 + 任务动作词时仍算任务（如「周三交」「作业 8.25 前」）
TASK_ACTION_WORDS = (
    "交", "提交", "截止", "作业", "实验", "报告", "考试", "测验", "验收",
    "答辩", "汇报", "presentation", "due", "ddl",
)
# 截图(OCR)通道额外动作词：OCR 可能丢失「辩」「截」等字（如「答辩时间」→「答时间」）
OCR_ACTION_WORDS = TASK_ACTION_WORDS + ("时间", "安排", "通知")
MATERIAL_HINTS = ("课件", "ppt", "pdf", "讲义", "笔记", "报告", "实验", "作业", "试卷", "习题")

TITLE_NOISE = re.compile(r"[，,。；;！!？?\s]*$|^[【\[\(（\s]*")


def _to_bool(v) -> bool:
    """LLM 可能回布尔或字符串（"true"/"false"），字符串需按值解析。"""
    if isinstance(v, bool):
        return v
    return str(v or "").strip().lower() in ("true", "1", "yes", "y")


def get_semester_anchor(db: Session, course_id: int | None = None) -> date | None:
    """学期锚点：优先课程起始日期，其次全局设置。"""
    if course_id:
        course = db.get(Course, course_id)
        if course and course.start_date:
            try:
                return date.fromisoformat(course.start_date)
            except ValueError:
                pass
    row = db.get(AppSetting, "semester_start")
    if row and row.value:
        try:
            return date.fromisoformat(row.value)
        except ValueError:
            pass
    return None


def _sentence_split(text: str) -> list[str]:
    parts = re.split(r"[。！？!?\n；;]+", text)
    return [p.strip() for p in parts if p.strip()]


def extract_by_rules(db: Session, text: str, course_hint: str = "",
                     source: str = "text") -> ExtractResult:
    """规则引擎：DDL 关键词句 + 时间表达式 + 词典打标。"""
    result = ExtractResult(engine="rule")
    seen_titles: set[str] = set()
    action_words = OCR_ACTION_WORDS if source == "screenshot" else TASK_ACTION_WORDS
    # 截图通道：OCR 换行会割裂「日期/时刻」，整段一起解析；文本通道按句拆
    sentences = [text.strip()] if source == "screenshot" else _sentence_split(text)

    for sent in sentences:
        if len(sent) < 2 or len(sent) > 400:
            continue
        # 先匹配课程 → 拿到该课程的学期锚点 → 再解析时间（第N周需要锚点）
        course_id, course_name = match_course(db, course_hint or sent)
        parsed = parse_single(sent, get_semester_anchor(db, course_id))
        is_ddl = any(k in sent.lower() for k in DDL_HINTS)
        has_action = any(k in sent.lower() for k in action_words)
        has_time = parsed is not None
        # 触发条件：有时间 且 (有 DDL 词 或 有任务动作词)；纯「某天讲课/开会」不算任务
        if has_time and (is_ddl or (has_action and re.search(r"(周|月|日|号|天|点)", sent))):
            title = TITLE_NOISE.sub("", sent).strip("截止")
            title = title.strip("。！!？?；;").strip()
            if not title or title in seen_titles:
                continue
            seen_titles.add(title)
            if len(title) > 60:
                title = title[:60] + "…"
            # 置信度：有时间 + DDL 词最高；仅有时间次之
            conf = parsed.confidence
            if is_ddl:
                conf = min(conf + 0.05, 1.0)
            else:
                conf = min(conf, 0.75)
            result.tasks.append(TaskSuggestion(
                title=title,
                kind="exam" if any(k in sent for k in ("考试", "测验", "期末", "期中")) else
                     ("report" if any(k in sent for k in ("报告", "汇报", "答辩")) else "homework"),
                due_at=parsed.dt,
                due_expr=parsed.expr,
                course=course_name,
                confidence=round(conf, 2),
                note=parsed.reason,
                source_text=sent,
            ))

    # 资料建议：整段文本含资料词 → 生成一条资料
    if any(k in text.lower() for k in MATERIAL_HINTS):
        mtype, mconf = classify_material_type(text)
        course_id, course_name = match_course(db, course_hint or text)
        tags = extract_tags(text)
        title = re.split(r"[，,。；;\n]", text.strip())[0]
        result.materials.append(MaterialSuggestion(
            title=title[:80] or "未命名资料",
            type=mtype,
            course=course_name,
            chapter=extract_chapter(text),
            difficulty=classify_difficulty(text),
            exam_focus=has_exam_focus(text),
            tags=tags,
            summary=text[:300],
            confidence=round(mconf, 2),
        ))
    return result


def extract_by_llm(db: Session, text: str, course_hint: str = "") -> ExtractResult | None:
    """LLM 结构化抽取。失败返回 None（降级规则引擎）。"""
    if not llm.llm_available():
        return None
    courses = [c.name for c in db.execute(select(Course)).scalars().all()]
    today = date.today()
    system = (
        "你是学生学习助手，从用户输入的文本中抽取课程任务(DDL)与课程资料信息。"
        "严格输出 JSON："
        '{"tasks":[{"title":"任务名(去掉时间与语气词)","kind":"homework|exam|report|lab|review|other",'
        '"due_iso":"YYYY-MM-DDTHH:MM:SS 或空","due_expr":"原文时间表达",'
        '"course":"课程名或空","confidence":0到1}],'
        '"materials":[{"title":"资料名","type":"courseware|homework|lab|exam|notes|handout|other",'
        '"course":"课程名或空","chapter":"章节号与章节名(如:第3章 内存管理,没有则空)",'
        '"difficulty":"easy|medium|hard 或空(不确定就空)",'
        '"exam_focus":true/false(老师强调的考点/必考/常考内容为true),'
        '"tags":["关键词"],"summary":"一句话简介","confidence":0到1}]}'
        "规则：时间用 ISO 格式且必须是明确的绝对时间；不确定的不要编造，due_iso 留空、confidence 降低；"
        "chapter 只在原文明确出现章节号时填写；exam_focus 只在原文出现考点/必考/重点/常考等强调词时为 true；"
        "若没有任务或资料，对应数组为空。"
    )
    user = (
        f"今天是 {today.isoformat()}（{['周一','周二','周三','周四','周五','周六','周日'][today.weekday()]}）。\n"
        f"已知课程：{courses if courses else '无'}\n"
        f"课程线索：{course_hint or '无'}\n"
        f"用户输入：\n{text[:4000]}"
    )
    data = llm.chat_json(system, user)
    if not data:
        return None

    result = ExtractResult(engine="llm")
    # 用本地时间解析器校验 LLM 给出的 ISO 时间，双保险。
    # LLM 给的 naive ISO 视为**设备本地时间**（提示词以本地日期为锚），
    # 带时区则按偏移保留；随后经 Schema 统一换算 UTC。
    for t in data.get("tasks", []):
        if not isinstance(t, dict) or not t.get("title"):
            continue
        due = None
        iso = t.get("due_iso") or ""
        try:
            due = localize(datetime.fromisoformat(iso))
        except (ValueError, TypeError):
            if t.get("due_expr"):
                p = parse_single(str(t["due_expr"]), get_semester_anchor(db, None))
                if p:
                    due = p.dt
        conf = float(t.get("confidence", 0.5))
        if due is None:
            conf = min(conf, 0.4)
        course_id, course_name = match_course(db, str(t.get("course", "")))
        result.tasks.append(TaskSuggestion(
            title=str(t["title"])[:200],
            kind=str(t.get("kind", "homework")),
            due_at=due,
            due_expr=str(t.get("due_expr", "")) or iso,
            course=course_name or str(t.get("course", "")),
            confidence=round(conf, 2),
            source_text=str(t.get("source_text", "")),
        ))
    for m in data.get("materials", []):
        if not isinstance(m, dict) or not m.get("title"):
            continue
        course_id, course_name = match_course(db, str(m.get("course", "")))
        result.materials.append(MaterialSuggestion(
            title=str(m["title"])[:120],
            type=str(m.get("type", "other")),
            course=course_name or str(m.get("course", "")),
            chapter=str(m.get("chapter") or "")[:120],
            difficulty=normalize_difficulty(str(m.get("difficulty") or "")),
            exam_focus=_to_bool(m.get("exam_focus", False)),
            tags=[str(x) for x in m.get("tags", [])][:8],
            summary=str(m.get("summary", "")),
            confidence=round(float(m.get("confidence", 0.5)), 2),
        ))
    return result


def _title_key(title: str) -> str:
    return re.sub(r"[\s，,。；;！!？?：:、\"'《》【】（）()]", "", title)[:12]


def _merge_results(rule: ExtractResult, llm_res: ExtractResult | None) -> ExtractResult:
    """合并双通道结果：按标题去重，保留置信度更高者。"""
    if llm_res is None:
        return rule
    merged = ExtractResult(engine="rule+llm")
    tasks: dict[str, TaskSuggestion] = {}

    def add_task(t: TaskSuggestion):
        k = _title_key(t.title)
        if k and (k not in tasks or tasks[k].confidence < t.confidence):
            tasks[k] = t

    for t in rule.tasks + llm_res.tasks:
        add_task(t)
    merged.tasks = sorted(tasks.values(), key=lambda x: x.confidence, reverse=True)

    mats: dict[str, list[MaterialSuggestion]] = {}
    for m in rule.materials + llm_res.materials:
        k = _title_key(m.title)
        if k:
            mats.setdefault(k, []).append(m)
    merged.materials = []
    for k, cands in mats.items():
        best = max(cands, key=lambda x: x.confidence)
        # 低置信候选补全高置信候选缺失的字段（章节/难度/考点）
        for other in cands:
            if other is best:
                continue
            if not best.chapter and other.chapter:
                best.chapter = other.chapter
            if not best.difficulty and other.difficulty:
                best.difficulty = other.difficulty
            if not best.exam_focus and other.exam_focus:
                best.exam_focus = True
        merged.materials.append(best)
    merged.warnings = rule.warnings + (llm_res.warnings if llm_res else [])
    # 无绝对时间的任务：警告提示需人工补时间
    for t in merged.tasks:
        if t.due_at is None and t.confidence >= 0.3:
            merged.warnings.append(f"「{t.title}」未识别到明确时间，请手动补充")
    return merged


def extract(db: Session, text: str, course_hint: str = "", source: str = "text") -> ExtractResult:
    """主入口：规则 + LLM 合并。文本可为文件名、正文或 OCR 结果。"""
    rule = extract_by_rules(db, text, course_hint, source)
    llm_res = extract_by_llm(db, text, course_hint)
    return _merge_results(rule, llm_res)


def extract_from_filename(db: Session, filename: str) -> ExtractResult:
    """文件名通道：去扩展名后的规则抽取（文件名通常短，规则足够）。"""
    stem = re.sub(r"\.[a-zA-Z0-9]+$", "", filename)
    stem = stem.replace("_", " ").replace("-", " ").strip()
    result = extract_by_rules(db, stem)
    # 文件名整体也是一条资料建议
    mtype, mconf = classify_material_type(stem)
    course_id, course_name = match_course(db, stem)
    if not any(_title_key(m.title) == _title_key(stem) for m in result.materials):
        result.materials.insert(0, MaterialSuggestion(
            title=stem[:120] or filename,
            type=mtype,
            course=course_name,
            chapter=extract_chapter(stem),
            difficulty=classify_difficulty(stem),
            exam_focus=has_exam_focus(stem),
            tags=extract_tags(stem),
            confidence=mconf,
        ))
    result.engine = "rule" if result.engine == "rule" else result.engine
    return result
