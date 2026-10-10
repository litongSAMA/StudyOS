/** 课表时段：星期 + 起止时刻（本地墙上时间 HH:MM）+ 上课周次 */
export interface CourseSlot {
  /** 1=周一 … 7=周日 */
  weekday: number;
  start: string;
  end: string;
  /** 上课周次（空 = 未注明） */
  weeks: number[];
  location: string;
  teacher: string;
}

export interface Course {
  id: number;
  name: string;
  code: string;
  semester: string;
  start_date: string | null;
  color: string;
  /** 课表：任课老师 / 上课地点 / 每周上课时段 */
  teacher: string;
  location: string;
  slots: CourseSlot[];
  created_at: string;
}

/** 导入课表：预览返回的单门课程条目 */
export interface CourseImportEntry {
  name: string;
  code: string;
  teacher: string;
  location: string;
  slots: CourseSlot[];
  /** 本机是否已有同名课程（导入时按名合并，不重复建课） */
  exists: boolean;
  existing_id: number | null;
  color: string;
}

/** 课表导入识别到的来源格式（后端可能扩展，未列出的取值按字符串兜底展示） */
export type CourseImportFormat =
  | "ics"
  | "json"
  | "excel"
  | "csv"
  | "image"
  | "pdf"
  | "html"
  | "word"
  | "ods";

export interface CourseImportPreview {
  format: CourseImportFormat;
  /** 来源格式中文名（如「PDF 课表」）；可能缺失/为空，展示时用 format 兜底 */
  format_label?: string;
  /** 学期锚点（最早上课周周一 / JSON 声明的开学日） */
  term_start_date: string | null;
  semester: string;
  /** 图片课表是否由 LLM 智能兜底识别（识别方式提示用） */
  ai_assisted?: boolean;
  entries: CourseImportEntry[];
}

export interface CourseImportResult {
  created: Course[];
  updated: Course[];
}

export interface Material {
  id: number;
  course_id: number | null;
  course_name: string | null;
  title: string;
  type: string;
  /** 独立章节字段（如「第3章 内存管理」），入库自动抽取、可手动修正 */
  chapter: string;
  /** 难度："" 未标注 / easy 简单 / medium 中等 / hard 困难 */
  difficulty: string;
  /** 考试重点：老师强调的考点/必考内容 */
  exam_focus: boolean;
  file_path: string;
  file_name: string;
  source: string;
  text: string;
  tags: string[];
  confidence: number;
  created_at: string;
}

export interface Task {
  id: number;
  course_id: number | null;
  course_name: string | null;
  title: string;
  kind: string;
  due_at: string;
  done: boolean;
  remind_offsets_h: number[];
  note: string;
  source_text: string;
  source_material_id: number | null;
  confidence: number;
  created_at: string;
}

export interface TaskSuggestion {
  title: string;
  kind: string;
  due_at: string | null;
  due_expr: string;
  course: string;
  confidence: number;
  note: string;
  source_text: string;
}

export interface MaterialSuggestion {
  title: string;
  type: string;
  course: string;
  chapter: string;
  difficulty: string;
  exam_focus: boolean;
  tags: string[];
  summary: string;
  confidence: number;
}

export interface ExtractResult {
  tasks: TaskSuggestion[];
  materials: MaterialSuggestion[];
  warnings: string[];
  engine: string;
}

/** 格式目录里的单个格式（GET /api/ingest/formats → groups[].formats[]） */
export interface FormatInfo {
  /** 规范扩展名，含点，如 ".pdf" */
  ext: string;
  /** 中文名，如「PDF 文档」 */
  label: string;
  /** 本机是否真的能抽取内容（false = 认得出来但做不了，前端灰显） */
  supported: boolean;
  /** 是否走 OCR 识别 */
  ocr: boolean;
  /** 备注/提示（如「扫描版自动转 OCR」），可能为空 */
  note: string;
  /** 所属分组 id */
  group: string;
}

export interface FormatGroup {
  id: string;
  label: string;
  formats: FormatInfo[];
}

/** GET /api/ingest/formats 的完整载荷（格式目录） */
export interface FormatCatalog {
  groups: FormatGroup[];
  /** 可直接喂给 <input accept> 的扩展名清单（已剔除本机不可解析的格式） */
  accept: string;
  /** 「AI 笔记提炼」可直传的 accept（文档/表格/图片…，剔除音视频与未知类型） */
  refine_accept?: string;
  extensions: string[];
  /** 别名 → 规范扩展名（`.tgz` → `.tar.gz`），前端归并同一格式的不同写法 */
  aliases?: Record<string, string>;
  unknown_note: string;
  ocr_available: boolean;
}

/** 上传回包里的格式判定结果：FormatInfo + 内容嗅探字段（接口可能返回 null） */
export interface DetectedFormat extends FormatInfo {
  group_label?: string;
  /** 抽取器 kind，如 pdf / docx / ocr */
  kind?: string;
  /** 按内容嗅探出的扩展名 */
  detected_ext?: string;
  /** 文件名/请求声明的扩展名 */
  declared_ext?: string;
  /** 容器家族：zip / ole2 / "" */
  container?: string;
  /** 扩展名与实际内容不一致（后端已按实际内容解析） */
  mismatch?: boolean;
  /** 是否由内容而非扩展名判定 */
  sniffed?: boolean;
}

export interface SuggestionBundle {
  extract: ExtractResult;
  file_name: string;
  extracted_text_preview: string;
  stored_path: string;
  /** 上传文件的格式判定；未知类型时为 null，前端需兜底 */
  format?: DetectedFormat | null;
  /** 抽取出的正文字数（缺省时前端不展示） */
  text_chars?: number;
}

export interface ApplyResult {
  created_tasks: Task[];
  created_materials: Material[];
  matched_courses: Record<string, number>;
  operation_id?: string;
  replayed?: boolean;
  reused_tasks?: Task[];
  reused_materials?: Material[];
}

export interface AgentCapabilities {
  version: number;
  actor_id?: string;
  device_id?: string;
  conversations?: boolean;
  semantic?: boolean;
  idempotency: boolean;
  chat: boolean;
  undo: boolean;
  review_replan: boolean;
}

export interface AgentProposal {
  plan_id?: string;
  intent: string;
  summary?: string;
  message?: string;
  expires_at?: string;
  needs_selection?: boolean;
  candidates?: { id: number; title: string; due_at?: string; course?: string }[];
  warnings?: string[];
  blocked_reason?: string;
  result?: {
    task_id?: number;
    old_due_at?: string;
    new_due_at?: string;
    inherited_time?: boolean;
    review_plan_id?: number;
    sessions?: ReviewSession[];
    completed_hours?: number;
    scheduled_hours?: number;
    unallocated?: { scope: string; hours: number; deadline: string }[];
  };
}

export interface AgentOperation {
  id: string;
  kind: string;
  created_at: string;
  undone_at: string | null;
  response: { operation_id: string; summary?: string; changed_count?: number; replayed?: boolean };
}

export interface AgentReply {
  conversation_id?: string;
  revision?: number;
  turn_id?: string;
  engine: string;
  intent: string;
  reply: string;
  requires_confirmation: boolean;
  tools: string[];
  proposal?: AgentProposal;
  candidates?: { id: number; title: string }[];
  links?: { label: string; href: string }[];
  context?: {
    materials: { id: number; title: string; snippet: string; chapter: string; href: string }[];
    materials_total: number;
    retrieval_engine?: string;
    tasks: Task[];
    due_reviews: { material_id: number; title: string; mastery: number; due_at: string }[];
    focus: { recent_minutes: number; recent_sessions: number };
    review_plans: ReviewPlan[];
  };
}

/** 提炼要点：point 文本 + importance（high/medium/low） */
export interface RefinePoint {
  point: string;
  importance: string;
}

/** 提炼结果（预览，未保存） */
export interface RefineResult {
  title: string;
  summary: string;
  points: RefinePoint[];
  mode: string;
  engine: string;
}

/** 文件提炼结果（识图 / 文档同一结构）：提炼结果 + 抽取出的原文 + 格式元信息 */
export interface FileRefineResult extends RefineResult {
  /** 抽取出的原文（图片=OCR 识别结果，文档=文本抽取结果，截断到 2 万字） */
  source_text: string;
  /** OCR 引擎是否可用（文档通道不受影响） */
  ocr_available: boolean;
  /** 原文来源：image=走 OCR / document=文本抽取 */
  source_kind: string;
  /** 原始文件名（已脱路径） */
  file_name: string;
  /** 按内容嗅探出的规范扩展名 */
  file_ext: string;
  /** 中文格式名，如「PDF 文档」 */
  format_label: string;
  /** 分组 id，如 document / sheet / image */
  format_group: string;
  /** 分组中文名，如「表格」 */
  format_group_label: string;
  /** 抽取到的全文总字数（回传的原文可能被截断） */
  text_chars: number;
  /** 回传原文是否被截断（超过 2 万字上限） */
  truncated: boolean;
  /** 抽取提示（扫描件 OCR / 扩展名与内容不一致 / 无文本层 …） */
  warnings: string[];
}

/** 识图提炼结果（`/notes/refine-image` 的历史结构，字段与文件提炼一致） */
export type ImageRefineResult = FileRefineResult;

/** 已保存的提炼笔记 */
export interface Note {
  id: number;
  material_id: number | null;
  material_title: string;
  course_name: string;
  title: string;
  mode: string;
  summary: string;
  points: RefinePoint[];
  engine: string;
  created_at: string;
}

export interface ReviewPlan {
  id: number;
  title: string;
  exam_task_ids: number[];
  sessions: ReviewSession[];
  created_at: string;
}

export interface ReviewSession {
  date: string;
  course: string;
  scope: string;
  material_ids: number[];
  materials: string[];
  hours: number;
  /** 该课程平均掌握程度（0~1，生成时快照） */
  mastery?: number;
  exam: string;
  exam_date: string;
  /** 本次新学/再学的资料 */
  study_ids?: number[];
  study_titles?: string[];
  /** 到期的间隔复习（来自复习状态 due_at） */
  due_ids?: number[];
  due_titles?: string[];
  /** 计划预排的间隔复习检查点（首次学习后 1/3/7 天） */
  spaced_ids?: number[];
  spaced_titles?: string[];
}

/** 资料复习状态：掌握程度 + 间隔重复调度（SM-2 变体） */
export interface ReviewState {
  material_id: number;
  material_title: string;
  course_id: number | null;
  course_name: string;
  /** 0~1 连续掌握程度（0 未掌握，1 完全掌握） */
  mastery: number;
  /** 连续成功复习次数 */
  repetitions: number;
  /** 遗忘次数 */
  lapses: number;
  /** 当前间隔（天） */
  interval_days: number;
  /** SM-2 难度系数 */
  ease: number;
  /** 下次应复习时刻（间隔重复到期点） */
  due_at: string | null;
  last_reviewed_at: string | null;
  last_rating: string;
  /** 间隔复习今日/已到期 */
  is_due: boolean;
  /** 到期后逾期天数 */
  overdue_days: number;
}

/** 间隔重复评分四档 */
export const RATINGS: Record<string, { label: string; cls: string; active: string }> = {
  again: { label: "忘了", cls: "text-red-600 border-red-200 hover:bg-red-50", active: "bg-red-600 border-red-600 text-white" },
  hard: { label: "吃力", cls: "text-orange-600 border-orange-200 hover:bg-orange-50", active: "bg-orange-500 border-orange-500 text-white" },
  good: { label: "正常", cls: "text-blue-600 border-blue-200 hover:bg-blue-50", active: "bg-blue-600 border-blue-600 text-white" },
  easy: { label: "轻松", cls: "text-emerald-600 border-emerald-200 hover:bg-emerald-50", active: "bg-emerald-600 border-emerald-600 text-white" },
};

/** 掌握程度 → 人话标签 */
export function masteryLabel(mastery: number): string {
  if (mastery <= 0) return "未复习";
  if (mastery < 0.4) return "初学";
  if (mastery < 0.85) return "熟练";
  return "已掌握";
}

/** 错题（错题本）：题目/我的答案/正确答案/错因 + 归档字段 + 掌握状态 */
export interface WrongQuestion {
  id: number;
  course_id: number | null;
  course_name: string | null;
  title: string;
  question: string;
  my_answer: string;
  answer: string;
  reason: string;
  source: string;
  chapter: string;
  difficulty: string;
  tags: string[];
  /** open 待攻克 / done 已掌握 */
  status: string;
  /** 累计重练次数 */
  review_count: number;
  /** 累计答错次数 */
  wrong_count: number;
  /** 连续答对次数（≥2 → 已掌握） */
  streak: number;
  last_result: string;
  last_reviewed_at: string | null;
  created_at: string;
}

/** 错题本统计 */
export interface WrongBookStats {
  total: number;
  open: number;
  done: number;
  /** 已掌握占比 0~100 */
  mastered_pct: number;
  by_course: { course_id: number | null; course_name: string; total: number; open: number; done: number }[];
}

/** 错题 AI 提炼结果（不落库，供表单预填核对后保存） */
export interface WrongRefineResult {
  title: string;
  question: string;
  /** 从原文里识别的「当时的错误答案」 */
  my_answer: string;
  /** 正确答案与解析 */
  answer: string;
  /** 错因分析 */
  reason: string;
  chapter: string;
  difficulty: string;
  tags: string[];
  /** rule 离线规则 / llm AI 模型 */
  engine: string;
}

/** 错题本过滤聚合：章节（自然排序）+ 标签云 */
export interface WrongBookFacets {
  chapters: ChapterFacet[];
  tags: TagFacet[];
}

// ---- AI 自测 / 抽背 ----
/** 一道自测题：choice 选择题 / qa 问答题 */
export interface QuizQuestion {
  /** choice / qa */
  type: string;
  stem: string;
  /** choice 为 3-5 个选项；qa 为空数组 */
  options: string[];
  /** choice = 正确选项字母（A-E）；qa = 参考答案要点 */
  answer: string;
  /** choice = 正确选项下标；qa = -1 */
  answer_index: number;
  explanation: string;
  /** 出题依据的原文句子（可追溯） */
  source: string;
}

/** 一题的作答记录（只存答过的题，index 为题号） */
export interface QuizResult {
  index: number;
  /** correct/wrong（选择题服务端判分）· self_ok/self_wrong（问答自评） */
  verdict: string;
  /** 选择题所选字母 */
  chosen: string;
  /** 问答题回忆内容 */
  answer_text: string;
  answered_at: string;
}

/** 一组 AI 自测（基于笔记/资料/文本生成，含作答记录） */
export interface Quiz {
  id: number;
  course_id: number | null;
  course_name: string | null;
  material_id: number | null;
  material_title: string;
  note_ids: number[];
  title: string;
  /** choice 选择题 / qa 问答题 / mixed 混合 */
  qtype: string;
  questions: QuizQuestion[];
  results: QuizResult[];
  /** rule 离线规则 / llm AI 模型 */
  engine: string;
  created_at: string;
  /** 以下为服务端计算字段 */
  answered: number;
  total: number;
  correct: number;
  wrong: number;
  score_pct: number;
}

/** 自测统计：组数/已答题目/选择题正确率/问答自评通过率 */
export interface QuizStats {
  total: number;
  questions: number;
  answered: number;
  correct: number;
  choice_total: number;
  choice_answered: number;
  choice_correct: number;
  choice_pct: number;
  qa_total: number;
  qa_answered: number;
  qa_ok: number;
  qa_pct: number;
  by_course: {
    course_id: number | null;
    course_name: string;
    total: number;
    answered: number;
    correct: number;
  }[];
}

/** 自测题型 → 文案 */
export const QUIZ_TYPES: Record<string, string> = {
  choice: "选择题",
  qa: "问答题",
  mixed: "混合",
};

/** 作答判定 → 文案与配色 */
export const QUIZ_VERDICTS: Record<string, { label: string; cls: string }> = {
  correct: { label: "答对", cls: "text-emerald-600" },
  wrong: { label: "答错", cls: "text-red-600" },
  self_ok: { label: "记住了", cls: "text-emerald-600" },
  self_wrong: { label: "没记住", cls: "text-red-600" },
};

/** 错题状态 → 文案与配色 */
export const WRONG_STATUS: Record<string, { label: string; cls: string }> = {
  open: { label: "待攻克", cls: "bg-red-100 text-red-600" },
  done: { label: "已掌握", cls: "bg-emerald-100 text-emerald-600" },
};

export interface Stats {
  materials: number;
  tasks_open: number;
  tasks_due_7d: number;
  tasks_overdue: number;
  courses: number;
  wrong_questions: number;
  wrong_questions_open: number;
  /** AI 自测组数 */
  quizzes: number;
  /** 已全部作答的自测组数 */
  quizzes_completed: number;
  upcoming: Task[];
  /** 番茄钟：今日自然完成的番茄数 */
  focus_count_today: number;
  /** 番茄钟：今日专注分钟 */
  focus_minutes_today: number;
  /** 番茄钟：连续专注天数 */
  focus_streak: number;
}

// ---- 番茄钟 / 专注计时 ----
/** 一次专注/休息会话（后端持久化，换浏览器不丢） */
export interface FocusSession {
  id: number;
  /** focus 专注 / short 短休息 / long 长休息 */
  mode: string;
  /** 计划分钟 */
  planned_min: number;
  /** 实际专注分钟 */
  duration_min: number;
  /** 自然完成 / 放弃·跳过 */
  completed: boolean;
  /** 本次专注期间记录打断的次数 */
  interrupt_count: number;
  task_id: number | null;
  task_title: string;
  course_id: number | null;
  course_name: string;
  plan_id: number | null;
  plan_title: string;
  /** 复习计划中的复习块日期 YYYY-MM-DD */
  plan_date: string;
  material_ids: number[];
  note: string;
  started_at: string;
  created_at: string;
}

/** 单日专注：番茄数 + 分钟 */
export interface FocusDayStat {
  date: string;
  count: number;
  minutes: number;
}

/** 番茄钟统计聚合（今日 / 近 N 天 / 连续天数 / 累计 / 课程分布 / 复习块投入） */
export interface FocusStats {
  today: FocusDayStat;
  week: FocusDayStat[];
  streak: number;
  total_count: number;
  total_minutes: number;
  by_course: { course_id: number | null; course_name: string; count: number; minutes: number }[];
  by_plan_date: { plan_id: number; plan_date: string; count: number; minutes: number }[];
}

export interface SearchResult {
  materials: Material[];
  /** 过滤后的真实命中总数（与 limit 无关） */
  total: number;
  snippet: string[];
  /** 语义检索相似度（0~1），与 materials 对齐；全文模式为 null */
  scores: (number | null)[];
  /** 实际引擎：fts / semantic:tfidf / semantic:api:xxx / hybrid:… */
  engine: string;
  /** 本次实际返回条数 */
  returned: number;
  /** 是否被 limit 截断（total > returned） */
  truncated: boolean;
}

/** 标签聚合项：全库标签及其资料数（后端 SQL GROUP BY 统计） */
export interface TagFacet {
  tag: string;
  count: number;
}

/** 章节聚合项：全库章节及其资料数（按章节号自然排序） */
export interface ChapterFacet {
  chapter: string;
  count: number;
}

/** 资料列表分页元信息（来自响应头） */
export interface PageMeta {
  total: number;
  limit: number;
  offset: number;
  hasMore: boolean;
  /** 标签过滤是否在 SQL 内完成（memory = 极端环境兜底） */
  tagFilter: "sql" | "memory";
}

export interface MaterialPage {
  items: Material[];
  meta: PageMeta;
}

/** 资料批量操作结果：实际变更条数 + 未找到的 id 数 */
export interface MaterialBatchResult {
  updated: number;
  deleted: number;
  missing: number;
}

/** 导出流水（同时是增量导出基线） */
export interface ExportRecord {
  id: number;
  kind: string;
  format: string;
  file_path: string;
  item_count: number;
  incremental: boolean;
  since_at: string | null;
  until_at: string | null;
  created_at: string;
}

export interface ExportFormatStatus {
  format: string;
  last: ExportRecord | null;
  /** 自该格式上次导出以来的变更条数（无记录时等于 total） */
  changed: number;
}

export interface ExportKindStatus {
  kind: string;
  label: string;
  total: number;
  last_export: ExportRecord | null;
  formats: ExportFormatStatus[];
}

export interface ExportStatus {
  kinds: ExportKindStatus[];
  records: ExportRecord[];
  now: string;
}

export interface SemanticStatus {
  backend: string;
  model: string;
  vectors: number;
  materials: number;
  llm_expansion: boolean;
  error: string;
}

/** 单门课的考试进度预测（DDL 倒排 + 与复习计划同一套时长公式） */
export interface CourseForecast {
  task_id: number;
  course_id: number | null;
  course_name: string;
  exam_title: string;
  due_at: string;
  /** 考试日（本地日期 YYYY-MM-DD） */
  due_date: string;
  days_left: number;
  materials_total: number;
  materials_studied: number;
  materials_mastered: number;
  /** 该课平均掌握程度（0~1） */
  current_mastery: number;
  total_hours: number;
  remaining_hours: number;
  /** 已完成进度（0~100） */
  progress_pct: number;
  /** 需要保持的每日学习小时 */
  required_pace_h: number;
  /** 预计完成日期（无法按时为 null） */
  predicted_finish_date: string | null;
  /** 预计考试日的掌握程度（0~1） */
  predicted_mastery: number;
  /** done / on_track / tight / at_risk */
  status: string;
  message: string;
}

/** 单日负载：DDL 截止 + 复习计划学时 + 到期复习 */
export interface LoadDay {
  date: string;
  deadline_count: number;
  deadline_weight: number;
  plan_hours: number;
  review_count: number;
  review_hours: number;
  total_hours: number;
  /** ok / busy / overload */
  level: string;
  is_today: boolean;
}

/** 结构化告警：单日超载 / 连续高负载 / 考试撞车 */
export interface LoadWarning {
  date: string;
  level: string;
  message: string;
}

/** 进度曲线点：当天结束时的预计总掌握程度（按资料数加权） */
export interface ForecastPoint {
  date: string;
  mastery: number;
  hours_done: number;
  hours_remaining: number;
}

/** 近 7 天实际复习节奏 vs 所需节奏 */
export interface PaceInfo {
  reviews_last_7d: number;
  study_hours_last_7d: number;
  required_hours_per_day: number;
}

export interface ForecastOverall {
  /** idle / done / on_track / tight / at_risk */
  status: string;
  label: string;
  message: string;
  courses_total: number;
  courses_at_risk: number;
  remaining_hours: number;
  busy_days: number;
}

/** 学习进度预测 / 负载预警总结果 */
export interface Forecast {
  now: string;
  hours_per_day: number;
  window_days: number;
  overall: ForecastOverall;
  courses: CourseForecast[];
  load_days: LoadDay[];
  warnings: LoadWarning[];
  curve: ForecastPoint[];
  pace: PaceInfo;
}

/** 预测状态 → 文案与配色 */
export const FORECAST_STATUS: Record<string, { label: string; cls: string; badge: string; bar: string }> = {
  idle: { label: "待配置", cls: "text-slate-500", badge: "bg-slate-100 text-slate-600", bar: "bg-slate-300" },
  done: { label: "已掌握", cls: "text-emerald-600", badge: "bg-emerald-100 text-emerald-700", bar: "bg-emerald-500" },
  on_track: { label: "进度良好", cls: "text-emerald-600", badge: "bg-emerald-100 text-emerald-700", bar: "bg-emerald-500" },
  tight: { label: "进度偏紧", cls: "text-amber-600", badge: "bg-amber-100 text-amber-700", bar: "bg-amber-500" },
  at_risk: { label: "进度预警", cls: "text-red-600", badge: "bg-red-100 text-red-700", bar: "bg-red-500" },
};

/** 负载等级 → 文案与配色 */
export const LOAD_LEVELS: Record<string, { label: string; cls: string; bar: string }> = {
  ok: { label: "轻松", cls: "text-emerald-600", bar: "bg-emerald-400" },
  busy: { label: "偏满", cls: "text-amber-600", bar: "bg-amber-400" },
  overload: { label: "超载", cls: "text-red-600", bar: "bg-red-500" },
};

// ---- 学习报告 / 学期统计 ----
/** 逐周时间线点：掌握度趋势 / 复习次数 / 错题攻克率 三个图共用 */
export interface ReportSeriesPoint {
  /** 周一日期 YYYY-MM-DD */
  date: string;
  /** 周末平均掌握程度（0~1，全学期资料口径，未复习按 0） */
  mastery: number;
  /** 本周间隔重复评分次数 */
  reviews: number;
  /** 累计评分次数 */
  reviews_cum: number;
  /** 本周新增错题 */
  new_wrong: number;
  /** 本周攻克（已掌握）错题 */
  conquered: number;
  /** 累计攻克 */
  conquered_cum: number;
  /** 累计攻克率（0~100） */
  wrong_rate: number;
  /** 错题总数（恒定） */
  wrong_total: number;
}

/** 一个学期（或全库）的汇总指标 */
export interface ReportStats {
  courses: number;
  materials: number;
  materials_reviewed: number;
  materials_mastered: number;
  /** 平均掌握程度（0~1） */
  mastery: number;
  /** 复习评分总次数 */
  reviews: number;
  wrong_total: number;
  wrong_done: number;
  /** 攻克率（0~100） */
  wrong_rate: number;
  tasks_total: number;
  tasks_done: number;
  /** 完成率（0~100） */
  tasks_rate: number;
}

/** 学期视图：指标 + 逐周时间线 */
export interface ReportSemester {
  /** 原始 semester 字段（空串 = 「全部学期」视图） */
  semester: string;
  /** 展示名（semester 或「未分学期」/「全部学期」） */
  label: string;
  course_names: string[];
  stats: ReportStats;
  series: ReportSeriesPoint[];
  /** 时间线起点（本地日期，无数据为空） */
  span_from: string;
  span_to: string;
}

/** 学习报告总结果：学期分组 + 全库汇总 */
export interface Report {
  history_note?: string;
  now: string;
  semesters: ReportSemester[];
  overview: ReportSemester;
}

export const MATERIAL_TYPES: Record<string, string> = {
  courseware: "课件",
  homework: "作业",
  lab: "实验",
  exam: "试卷",
  notes: "笔记",
  handout: "讲义",
  report: "报告",
  other: "其他",
};

/** 难度标签："" 未标注 / easy / medium / hard */
export const DIFFICULTIES: Record<string, string> = {
  "": "未标注",
  easy: "简单",
  medium: "中等",
  hard: "困难",
};

/** 难度徽章配色（简单绿 / 中等黄 / 困难红） */
export const DIFF_COLORS: Record<string, string> = {
  easy: "bg-emerald-100 text-emerald-700",
  medium: "bg-amber-100 text-amber-700",
  hard: "bg-red-100 text-red-700",
  "": "bg-slate-100 text-slate-500",
};

export const TASK_KINDS: Record<string, string> = {
  homework: "作业",
  exam: "考试",
  report: "报告",
  lab: "实验",
  review: "复习",
  other: "其他",
};

export const KIND_COLORS: Record<string, string> = {
  homework: "bg-blue-100 text-blue-700",
  exam: "bg-red-100 text-red-700",
  report: "bg-amber-100 text-amber-700",
  lab: "bg-emerald-100 text-emerald-700",
  review: "bg-violet-100 text-violet-700",
  other: "bg-slate-100 text-slate-600",
};

/** 笔记提炼模式：keypoints 重点笔记 / exam 考点笔记 */
export const NOTE_MODES: Record<string, string> = {
  keypoints: "重点笔记",
  exam: "考点笔记",
};

/** 要点重要性徽章配色（高红 / 中黄 / 低灰） */
export const IMPORTANCE_COLORS: Record<string, string> = {
  high: "bg-red-100 text-red-700",
  medium: "bg-amber-100 text-amber-700",
  low: "bg-slate-100 text-slate-500",
};

export const IMPORTANCE_LABELS: Record<string, string> = {
  high: "高",
  medium: "中",
  low: "低",
};

/** 清单项：文本 + 勾选状态 + 备注 */
export interface ChecklistItem {
  text: string;
  done: boolean;
  note: string;
}

/** 通用勾选清单：标题/描述/课程/标签/截止日 + 可勾选项 */
export interface Checklist {
  id: number;
  course_id: number | null;
  course_name: string | null;
  title: string;
  description: string;
  tags: string[];
  due_at: string | null;
  items: ChecklistItem[];
  done_count: number;
  total_count: number;
  created_at: string;
  updated_at: string;
}

/** 模版库条目：内置（key 非空、只读）或用户自存（可编辑删除） */
export interface ChecklistTemplate {
  id: number;
  key: string | null;
  title: string;
  description: string;
  category: string;
  tags: string[];
  items: ChecklistItem[];
  builtin: boolean;
  source_checklist_id: number | null;
  created_at: string;
  updated_at: string;
}

/** 分享链接信息（管理侧） */
export interface ShareInfo {
  token: string;
  checklist_id: number;
  checklist_title: string;
  url: string;
  api_url: string;
  expires_at: string | null;
  created_at: string;
  view_count: number;
  revoked: boolean;
}

/** 公开分享视图（只读清单快照） */
export interface SharedChecklist {
  token: string;
  title: string;
  description: string;
  course_name: string;
  tags: string[];
  items: ChecklistItem[];
  due_at: string | null;
  created_at: string;
  expires_at: string | null;
  view_count: number;
  is_owner: boolean;
}
