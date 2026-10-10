"""API 请求/响应 Schema。

datetime 字段一律使用 `UTCDateTime`（timeutil）：输入（Z/±HH:MM/无后缀 ISO）
统一归一化为 naive UTC 入库，JSON 输出统一带 `Z` 后缀——前端 `new Date()`
按浏览器本地时区展示，跨时区双设备同步不再互相偏移。
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.infra.timeutil import UTCDateTime


class CourseSlot(BaseModel):
    """课表时段：星期 + 起止时刻（本地墙上时间 HH:MM）+ 上课周次。"""

    weekday: int = Field(ge=1, le=7)  # 1=周一 … 7=周日
    start: str = ""                   # "HH:MM"
    end: str = ""                     # "HH:MM"
    weeks: list[int] = []             # 上课周次（空 = 未注明）
    location: str = ""                # 该时段上课地点（可覆盖课程默认地点）
    teacher: str = ""                 # 该时段任课老师（可覆盖课程默认老师）


class CourseIn(BaseModel):
    name: str
    code: str = ""
    semester: str = ""
    start_date: str | None = None
    color: str = "#6366f1"
    teacher: str = ""
    location: str = ""
    slots: list[CourseSlot] = []


class CourseOut(CourseIn):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: UTCDateTime


class CoursePatch(BaseModel):
    """课程部分更新（PATCH）：全部可选，仅更新传入字段。"""

    name: str | None = None
    code: str | None = None
    semester: str | None = None
    start_date: str | None = None
    color: str | None = None
    teacher: str | None = None
    location: str | None = None
    slots: list[CourseSlot] | None = None


class CourseImportEntry(BaseModel):
    """导入课表：单门课程的待导入条目。"""

    name: str
    code: str = ""
    teacher: str = ""
    location: str = ""
    slots: list[CourseSlot] = []


class CourseImportEntryOut(CourseImportEntry):
    """预览返回：附带是否已存在同名课程（导入时按名合并）与展示用颜色。"""

    exists: bool = False
    existing_id: int | None = None
    color: str = "#6366f1"


class CourseImportPreview(BaseModel):
    format: str = "ics"               # ics / json / excel / csv / image / pdf / html / word / ods
    format_label: str = ""            # 来源格式中文名（如「PDF 课表」），前端展示用
    term_start_date: str | None = None  # 学期锚点（最早上课周周一 / JSON 声明的开学日）
    semester: str = ""
    ai_assisted: bool = False         # 图片课表是否走了 LLM 智能兜底识别
    entries: list[CourseImportEntryOut] = []


class CourseImportApply(BaseModel):
    """确认导入：提交勾选的条目（同名课程合并更新，新课程自动创建）。"""

    entries: list[CourseImportEntry]
    semester: str = ""
    start_date: str | None = None


class CourseImportResult(BaseModel):
    created: list[CourseOut] = []
    updated: list[CourseOut] = []


class MaterialIn(BaseModel):
    course_id: int | None = None
    title: str
    type: str = "other"
    chapter: str = ""                      # 章节（独立字段，如「第3章 内存管理」）
    difficulty: str = ""                   # 难度：""(未标注) / easy / medium / hard
    exam_focus: bool = False               # 考试重点标记
    tags: list[str] = []
    text: str = ""
    source: str = "text"
    confidence: float = 1.0


class MaterialOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    course_id: int | None
    course_name: str | None = None
    title: str
    type: str
    chapter: str = ""
    difficulty: str = ""
    exam_focus: bool = False
    file_path: str
    file_name: str
    source: str
    text: str = ""
    tags: list[str]
    confidence: float
    created_at: UTCDateTime


class MaterialPatch(BaseModel):
    course_id: int | None = None
    title: str | None = None
    type: str | None = None
    chapter: str | None = None
    difficulty: str | None = None
    exam_focus: bool | None = None
    tags: list[str] | None = None
    text: str | None = None  # 允许修正抽取文本（如 OCR 纠错），同步更新全文索引


# ---- 资料批量操作（大资料库整批整理）：删除 / 改标签 / 换课程 / 改归档字段 ----
# 一次上限 500 条：整批在一个事务里完成，中途失败不留半成品。
MAX_BATCH_IDS = 500


class MaterialBatch(BaseModel):
    """批量操作共同的入参：资料 id 列表（去重后上限 500 条）。"""

    ids: list[int]

    @field_validator("ids")
    @classmethod
    def _check_ids(cls, v: list[int]) -> list[int]:
        if not v:
            raise ValueError("请至少选择一条资料")
        if len(v) > MAX_BATCH_IDS:
            raise ValueError(f"一次最多批量处理 {MAX_BATCH_IDS} 条资料，请分批操作")
        return list(dict.fromkeys(v))  # 去重且保持顺序：重复 id 不会重复计数


class MaterialBatchTags(MaterialBatch):
    """批量改标签：add 追加 / remove 移除 / set 覆盖（set 空数组 = 清空）。"""

    tags: list[str] = []
    mode: Literal["add", "remove", "set"] = "add"


class MaterialBatchCourse(MaterialBatch):
    """批量换课程：course_id 为 null 表示移到「未分类」。"""

    course_id: int | None = None


class MaterialBatchFields(MaterialBatch):
    """批量设置归档字段：只改提交的非 null 字段；chapter="" / difficulty="" 表示清空。"""

    chapter: str | None = None
    difficulty: str | None = None
    exam_focus: bool | None = None
    type: str | None = None


class MaterialBatchResult(BaseModel):
    """批量操作结果：实际变更条数 + 未找到的 id 数（与前端 MaterialBatchResult 对齐）。"""

    updated: int = 0
    deleted: int = 0
    missing: int = 0


class TaskIn(BaseModel):
    course_id: int | None = None
    title: str
    kind: str = "homework"
    due_at: UTCDateTime
    remind_offsets_h: list[int] = [72, 24, 2]
    note: str = ""
    source_text: str = ""
    source_material_id: int | None = None
    confidence: float = 1.0


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    course_id: int | None
    course_name: str | None = None
    title: str
    kind: str
    due_at: UTCDateTime
    done: bool
    remind_offsets_h: list[int]
    note: str
    source_text: str
    source_material_id: int | None
    confidence: float
    created_at: UTCDateTime


class TaskPatch(BaseModel):
    course_id: int | None = None
    title: str | None = None
    kind: str | None = None
    due_at: UTCDateTime | None = None
    done: bool | None = None
    remind_offsets_h: list[int] | None = None
    note: str | None = None


class SearchResult(BaseModel):
    materials: list[MaterialOut]
    total: int
    snippet: list[str] = []
    # 语义检索：与 materials 对齐的相似度（0~1，全文模式为 None）
    scores: list[float | None] = []
    # 实际检索引擎：fts / semantic:api:xxx / semantic:tfidf / hybrid:api:xxx / hybrid:tfidf
    engine: str = "fts"
    # 本次实际返回条数与是否被 limit 截断（total 为过滤后的真实命中总数）
    returned: int = 0
    truncated: bool = False


class TagFacet(BaseModel):
    """标签聚合项：标签 + 该标签下的资料数（SQL GROUP BY 统计，覆盖全库）。"""

    tag: str
    count: int


class ChapterFacet(BaseModel):
    """章节聚合项：章节名 + 该章节下的资料数（按章节号自然排序）。"""

    chapter: str
    count: int


# ---- AI Agent 抽取建议 ----
class TaskSuggestion(BaseModel):
    title: str
    kind: str = "homework"
    due_at: UTCDateTime | None = None
    due_expr: str = ""          # 原文中的时间表达，供用户核对
    course: str = ""            # 建议的课程名（可为空）
    confidence: float = 1.0
    note: str = ""
    source_text: str = ""


class MaterialSuggestion(BaseModel):
    title: str
    type: str = "other"
    course: str = ""
    chapter: str = ""
    difficulty: str = ""
    exam_focus: bool = False
    tags: list[str] = []
    summary: str = ""
    confidence: float = 1.0


class ExtractResult(BaseModel):
    tasks: list[TaskSuggestion] = []
    materials: list[MaterialSuggestion] = []
    warnings: list[str] = []
    engine: str = "rule"        # rule / llm / rule+llm


class ApplyResult(BaseModel):
    created_tasks: list[TaskOut] = []
    created_materials: list[MaterialOut] = []
    matched_courses: dict[str, int] = {}


# ---- AI 笔记提炼 ----
class RefinePoint(BaseModel):
    point: str
    importance: str = "medium"      # high / medium / low


class RefineResult(BaseModel):
    title: str = ""
    summary: str = ""
    points: list[RefinePoint] = []
    mode: str = "keypoints"         # keypoints（重点）/ exam（考点）
    engine: str = "rule"            # rule（离线规则）/ llm（AI 模型）


class FileRefineResult(RefineResult):
    """文件提炼结果（识图 / 文档同一结构）：提炼结果 + 抽取出的原文 + 格式与抽取提示。

    `source_text` 回传前端原文，用于核对识别结果、切换模式时直接重新提炼
    （不必再次上传），也可人工修正后重提炼。
    """
    source_text: str = ""           # 抽取原文（截断到 20000 字）
    ocr_available: bool = True      # OCR 引擎是否可用
    source_kind: str = "document"   # image（走 OCR）/ document（文本抽取）
    file_name: str = ""             # 原始文件名（脱路径）
    file_ext: str = ""              # 按内容嗅探出的规范扩展名
    format_label: str = ""          # 中文格式名，如「PDF 文档」
    format_group: str = ""          # 分组 id，如 document / sheet / image
    format_group_label: str = ""    # 分组中文名，如「表格」
    text_chars: int = 0             # 抽取到的全文总字数（回传的原文可能被截断）
    truncated: bool = False         # 回传原文是否被截断（超过 20000 字上限）
    warnings: list[str] = []        # 抽取提示（扫描件 OCR / 扩展名与内容不一致 / …）


class ImageRefineResult(FileRefineResult):
    """识图提炼结果（`/refine-image` 的历史响应结构，字段与文件提炼一致）。"""


class NoteIn(BaseModel):
    material_id: int | None = None
    title: str
    mode: str = "keypoints"         # keypoints / exam
    summary: str = ""
    points: list[RefinePoint] = []
    engine: str = "rule"            # rule / llm


class NoteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    material_id: int | None
    material_title: str = ""        # 来源资料标题（资料已删除时为空）
    course_name: str = ""           # 来源资料所属课程（资料已删除时为空）
    title: str
    mode: str
    summary: str
    points: list[RefinePoint] = []
    engine: str
    created_at: UTCDateTime


class NotePatch(BaseModel):
    title: str | None = None
    mode: str | None = None         # keypoints / exam
    summary: str | None = None
    points: list[RefinePoint] | None = None


# ---- 错题归集（错题本） ----
class WrongQuestionIn(BaseModel):
    course_id: int | None = None
    title: str
    question: str = ""
    my_answer: str = ""
    answer: str = ""
    reason: str = ""
    source: str = ""
    chapter: str = ""               # 章节：留空自动从「标题 + 题干」规则抽取
    difficulty: str = ""            # 难度：留空自动判定，非法值 422
    tags: list[str] = []


class WrongQuestionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    course_id: int | None
    course_name: str | None = None
    title: str
    question: str = ""
    my_answer: str = ""
    answer: str = ""
    reason: str = ""
    source: str = ""
    chapter: str = ""
    difficulty: str = ""
    tags: list[str]
    status: str                    # open（待攻克）/ done（已掌握）
    review_count: int = 0          # 累计重练次数
    wrong_count: int = 1           # 累计答错次数
    streak: int = 0                # 连续答对次数（≥2 → done）
    last_result: str = ""
    last_reviewed_at: UTCDateTime | None = None
    created_at: UTCDateTime


class WrongQuestionPatch(BaseModel):
    course_id: int | None = None
    title: str | None = None
    question: str | None = None
    my_answer: str | None = None
    answer: str | None = None
    reason: str | None = None
    source: str | None = None
    chapter: str | None = None
    difficulty: str | None = None
    tags: list[str] | None = None
    status: str | None = None      # open / done（手动修正掌握状态）


class WrongPracticeIn(BaseModel):
    """重练自评结果：ok=True 答对了；False 又错了。"""

    ok: bool


class WrongRefineResult(BaseModel):
    """错题 AI 提炼结果（不落库，供表单预填核对后保存）。"""

    title: str = ""
    question: str = ""
    my_answer: str = ""             # 从原文里识别的「当时的错误答案」
    answer: str = ""                # 正确答案与解析
    reason: str = ""                # 错因分析
    chapter: str = ""               # 章节（""/easy/medium/hard 之外的难度归一化为空）
    difficulty: str = ""            # 难度："" / easy / medium / hard
    tags: list[str] = []
    engine: str = "rule"            # rule（离线规则）/ llm（AI 模型）


class WrongBookStats(BaseModel):
    """错题本统计：总数/待攻克/已掌握 + 按课程分布。"""

    total: int = 0
    open: int = 0
    done: int = 0
    mastered_pct: int = 0          # 已掌握占比（0~100，整数）
    by_course: list[dict] = []     # [{course_id, course_name, open, done}]


class WrongBookFacets(BaseModel):
    """错题本过滤聚合：章节 + 标签（全库 GROUP BY，不是当前页）。"""

    chapters: list[ChapterFacet] = []
    tags: list[TagFacet] = []


class ReviewIn(BaseModel):
    exam_task_ids: list[int]
    hours_per_day: float = 3.0


class ReviewRateIn(BaseModel):
    """间隔重复评分：again（忘了）/ hard（吃力）/ good（正常）/ easy（轻松）。"""

    material_id: int
    rating: str


class MaterialReviewOut(BaseModel):
    """资料复习状态（无状态记录的资料按 mastery=0「未复习」返回）。"""

    material_id: int
    material_title: str = ""
    course_id: int | None = None
    course_name: str = ""
    mastery: float = 0.0          # 0~1 连续掌握程度
    repetitions: int = 0          # 连续成功复习次数
    lapses: int = 0               # 遗忘次数
    interval_days: float = 0.0    # 当前间隔（天）
    ease: float = 2.5             # SM-2 难度系数
    due_at: UTCDateTime | None = None
    last_reviewed_at: UTCDateTime | None = None
    last_rating: str = ""
    is_due: bool = False          # 间隔复习今日/已到期
    overdue_days: int = 0         # 到期后逾期天数（0 = 未逾期）


class ReviewSession(BaseModel):
    date: str
    course: str
    scope: str
    material_ids: list[int] = []
    materials: list[str] = []
    hours: float
    mastery: float = 0.0          # 该课程当前平均掌握程度（0~1，生成时快照）
    exam: str = ""
    exam_date: str = ""
    study_ids: list[int] = []     # 本次新学/再学资料
    study_titles: list[str] = []
    due_ids: list[int] = []       # 到期的间隔复习（从复习状态 due_at 来）
    due_titles: list[str] = []
    spaced_ids: list[int] = []    # 计划预排的间隔复习检查点（首次学习后 1/3/7 天）
    spaced_titles: list[str] = []


class ReviewPlanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    title: str
    exam_task_ids: list[int]
    sessions: list[dict]
    created_at: UTCDateTime


class SettingIn(BaseModel):
    key: str
    value: str


# ---- 学习进度预测 / 负载预警 ----
class CourseForecast(BaseModel):
    """一门课的考试进度预测（DDL 倒排 + 与复习计划同一套时长公式）。"""

    task_id: int
    course_id: int | None = None
    course_name: str = ""
    exam_title: str
    due_at: UTCDateTime
    due_date: str = ""            # 考试日（本地日期 YYYY-MM-DD）
    days_left: int = 0
    materials_total: int = 0
    materials_studied: int = 0    # 已开始复习（mastery>0）
    materials_mastered: int = 0   # 已掌握（mastery≥0.85）
    current_mastery: float = 0.0  # 该课平均掌握程度（0~1）
    total_hours: float = 0.0      # 全部资料折算学时
    remaining_hours: float = 0.0  # 还需的新学学时
    progress_pct: float = 0.0     # 已完成进度（0~100）
    required_pace_h: float = 0.0  # 需要保持的每日学习小时
    predicted_finish_date: str | None = None  # 预计完成日期（无法按时为 None）
    predicted_mastery: float = 0.0  # 预计考试日的掌握程度（0~1）
    status: str = "on_track"      # done / on_track / tight / at_risk
    message: str = ""


class LoadDay(BaseModel):
    """单日负载：DDL 截止 + 复习计划学时 + 到期间隔复习。"""

    date: str
    deadline_count: int = 0
    deadline_weight: float = 0.0  # 按任务类型的负载权重和（考试 2.0 …）
    plan_hours: float = 0.0
    review_count: int = 0         # 当日到期的间隔复习资料数
    review_hours: float = 0.0
    total_hours: float = 0.0
    level: str = "ok"             # ok / busy / overload
    is_today: bool = False


class LoadWarning(BaseModel):
    """结构化告警：逐日负载 + 连续高负载 + 考试撞车。"""

    date: str = ""
    level: str = "busy"           # busy / overload
    message: str


class ForecastPoint(BaseModel):
    """进度曲线点：当天结束时的预计总掌握程度（按资料数加权）。"""

    date: str
    mastery: float = 0.0          # 0~1
    hours_done: float = 0.0
    hours_remaining: float = 0.0


class PaceInfo(BaseModel):
    """近 7 天实际复习节奏 vs 所需节奏。"""

    reviews_last_7d: int = 0          # 近 7 天间隔复习评分次数
    study_hours_last_7d: float = 0.0  # 按每次 0.5h 近似折算
    required_hours_per_day: float = 0.0  # 最紧课程的每日所需时长


class ForecastOverall(BaseModel):
    status: str = "idle"          # idle / done / on_track / tight / at_risk
    label: str = "待配置"
    message: str = ""
    courses_total: int = 0
    courses_at_risk: int = 0
    remaining_hours: float = 0.0
    busy_days: int = 0


class ForecastOut(BaseModel):
    now: UTCDateTime
    hours_per_day: float
    window_days: int
    overall: ForecastOverall
    courses: list[CourseForecast] = []
    load_days: list[LoadDay] = []
    warnings: list[LoadWarning] = []
    curve: list[ForecastPoint] = []
    pace: PaceInfo = PaceInfo()


class Stats(BaseModel):
    quizzes: int = 0
    quizzes_completed: int = 0
    focus_count_today: int = 0
    focus_minutes_today: float = 0
    focus_streak: int = 0
    materials: int
    tasks_open: int
    tasks_due_7d: int
    tasks_overdue: int
    courses: int
    wrong_questions: int = 0        # 错题本总数
    wrong_questions_open: int = 0   # 待攻克错题数
    upcoming: list[TaskOut] = []


class FormatInfo(BaseModel):
    """上传文件被识别出的真实格式（内容嗅探结果，供前端展示与提示）。"""

    ext: str = ""              # 规范扩展名
    label: str = ""            # 中文名（如「Word 文档」）
    group: str = ""            # 分组 id
    group_label: str = ""      # 分组中文名
    kind: str = ""             # 抽取器 kind
    ocr: bool = False          # 是否走 OCR 通道
    supported: bool = True     # 本机是否支持文本抽取
    note: str = ""             # 展示给用户的补充说明
    detected_ext: str = ""     # 内容嗅探出的扩展名（未知为空）
    declared_ext: str = ""     # 文件名声明的扩展名
    container: str = ""        # 容器家族：zip / ole2 / ""
    mismatch: bool = False     # 扩展名与内容不一致（已按内容解析）
    sniffed: bool = False      # 是否由内容而非扩展名判定


class IngestFormatItem(BaseModel):
    """格式目录里的一条格式。"""

    ext: str
    label: str
    supported: bool = True
    ocr: bool = False
    note: str = ""
    group: str = ""


class IngestFormatGroup(BaseModel):
    """格式目录里的一个分组。"""

    id: str
    label: str
    formats: list[IngestFormatItem] = []


class IngestFormatCatalog(BaseModel):
    """`GET /api/ingest/formats`：支持格式的单一事实来源（前端 accept/提示由此派生）。"""

    groups: list[IngestFormatGroup] = []
    accept: str = ""                      # 可直接用于 <input accept>
    refine_accept: str = ""               # 「AI 笔记提炼」可直传的 accept（剔除音视频等）
    extensions: list[str] = []
    aliases: dict[str, str] = {}          # 别名 → 规范扩展名（.tgz → .tar.gz）
    unknown_note: str = ""
    ocr_available: bool = False
    max_upload_mb: int = 512              # 单文件上传上限（前端提示用）
    pdf_ocr_text_min: int = 200           # 扫描版 PDF 自动 OCR 的文本量阈值


class SuggestionBundle(BaseModel):
    """入库解析结果 + 已存在资料/任务重复检测后的完整建议包。"""
    extract: ExtractResult
    file_name: str = ""
    extracted_text_preview: str = ""
    stored_path: str = ""  # 已保存文件的绝对路径，确认入库时回填
    format: FormatInfo | None = None  # 识别出的真实格式（未知类型为 None）
    text_chars: int = 0               # 抽取到的正文字符数（0 = 没抽到文本）


# ---- 导出记录与增量导出状态 ----
class ExportRecordOut(BaseModel):
    """一条导出流水（同时是增量导出的基线）。"""

    model_config = ConfigDict(from_attributes=True)
    id: int
    kind: str
    format: str
    file_path: str
    item_count: int = 0
    incremental: bool = False
    since_at: UTCDateTime | None = None
    until_at: UTCDateTime | None = None
    created_at: UTCDateTime


class ExportFormatStatus(BaseModel):
    """某类数据在某个格式下的导出状态：上次导出 + 自那以来的变更条数。"""

    format: str
    last: ExportRecordOut | None = None
    changed: int = 0  # 自该格式上次导出以来有变更的条目数（无记录时等于 total）


class ExportKindStatus(BaseModel):
    kind: str            # tasks / materials / review
    label: str
    total: int = 0       # 该类当前全部条目数
    last_export: ExportRecordOut | None = None  # 最近一次导出（不限格式）
    formats: list[ExportFormatStatus] = []


class ExportStatusOut(BaseModel):
    kinds: list[ExportKindStatus] = []
    records: list[ExportRecordOut] = []  # 最近若干条导出历史
    now: UTCDateTime


# ---- 清单 / 模版库 / 分享链接 ----
class ChecklistItem(BaseModel):
    text: str
    done: bool = False
    note: str = ""


class ChecklistIn(BaseModel):
    course_id: int | None = None
    title: str
    description: str = ""
    tags: list[str] = []
    due_at: UTCDateTime | None = None
    items: list[ChecklistItem] = []


class ChecklistOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    course_id: int | None
    course_name: str | None = None
    title: str
    description: str = ""
    tags: list[str] = []
    due_at: UTCDateTime | None = None
    items: list[ChecklistItem] = []
    done_count: int = 0      # 已勾选项数（序列化时计算）
    total_count: int = 0     # 总项数
    created_at: UTCDateTime
    updated_at: UTCDateTime


class ChecklistPatch(BaseModel):
    course_id: int | None = None
    title: str | None = None
    description: str | None = None
    tags: list[str] | None = None
    due_at: UTCDateTime | None = None
    items: list[ChecklistItem] | None = None


class ChecklistTemplateIn(BaseModel):
    title: str
    description: str = ""
    category: str = "日常学习"
    tags: list[str] = []
    items: list[ChecklistItem] = []


class ChecklistTemplateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    key: str | None = None
    title: str
    description: str = ""
    category: str = ""
    tags: list[str] = []
    items: list[ChecklistItem] = []
    builtin: bool = False
    source_checklist_id: int | None = None
    created_at: UTCDateTime
    updated_at: UTCDateTime


class ChecklistTemplatePatch(BaseModel):
    title: str | None = None
    description: str | None = None
    category: str | None = None
    tags: list[str] | None = None
    items: list[ChecklistItem] | None = None


class SaveAsTemplateIn(BaseModel):
    """把已有清单存为新模版（进模版库）。"""

    title: str | None = None
    category: str | None = None


class ShareCreate(BaseModel):
    """创建分享链接：expires_h 为空 = 永久有效；1~8760 小时（最长一年）。"""

    expires_h: int | None = None


class ShareOut(BaseModel):
    token: str
    checklist_id: int
    checklist_title: str = ""
    url: str = ""            # 分享页面链接（浏览器直接打开）
    api_url: str = ""        # 公开只读接口（跨端一键导入用）
    expires_at: UTCDateTime | None = None
    created_at: UTCDateTime
    view_count: int = 0
    revoked: bool = False


class SharedChecklistOut(BaseModel):
    """公开分享视图：只读清单快照 + 分享元信息。"""

    token: str
    title: str
    description: str = ""
    course_name: str = ""
    tags: list[str] = []
    items: list[ChecklistItem] = []
    due_at: UTCDateTime | None = None
    created_at: UTCDateTime
    expires_at: UTCDateTime | None = None
    view_count: int = 0
    is_owner: bool = False


class ChecklistImportIn(BaseModel):
    """导入清单：url（分享链接，服务端抓取）或 payload（下载的分享 JSON）。"""

    url: str | None = None
    payload: dict | None = None
