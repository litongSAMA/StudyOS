import type {
  ApplyResult,
  AgentCapabilities,
  AgentOperation,
  AgentReply,
  ChapterFacet,
  Checklist,
  ChecklistTemplate,
  Course,
  CourseImportEntry,
  CourseImportPreview,
  CourseImportResult,
  ExportRecord,
  ExportStatus,
  ExtractResult,
  FileRefineResult,
  FocusSession,
  FocusStats,
  Forecast,
  FormatCatalog,
  ImageRefineResult,
  Material,
  MaterialBatchResult,
  MaterialPage,
  MaterialSuggestion,
  Note,
  PageMeta,
  Quiz,
  QuizStats,
  RefineResult,
  Report,
  ReviewPlan,
  ReviewState,
  SearchResult,
  SemanticStatus,
  ShareInfo,
  SharedChecklist,
  Stats,
  SuggestionBundle,
  TagFacet,
  Task,
  TaskSuggestion,
  WrongBookFacets,
  WrongBookStats,
  WrongQuestion,
  WrongRefineResult,
} from "./types";
import { beginWrite, finishWrite, setWriteIdentity, loadPendingWrite, type AgentWrite } from "./agentWrites.ts";

const BASE = "/api";

// ---- 登录会话令牌（本地账号体系）----
// 令牌走 Authorization 头（自定义头不随跨站请求自动携带，天然防 CSRF），
// 存 localStorage：换页/刷新仍保持登录；登出或 401 时清除并广播。
const AUTH_PROTECTION_KEY="studyos.auth.protected";
export function getKnownAuthProtection(){ try {return localStorage.getItem(AUTH_PROTECTION_KEY)==="1";}catch{return false;} }
const AUTH_TOKEN_KEY = "studyos.auth.token";
let authToken: string | null = (() => {
  try {
    return localStorage.getItem(AUTH_TOKEN_KEY);
  } catch {
    return null;
  }
})();
export const getAuthToken = () => authToken;
export function setAuthToken(token: string | null) {
  authToken = token;
  setWriteIdentity(null);
  try {
    if (token) localStorage.setItem(AUTH_TOKEN_KEY, token);
    else localStorage.removeItem(AUTH_TOKEN_KEY);
  } catch {
    /* ignore */
  }
}
const authExpiredListeners = new Set<() => void>();
/** 订阅「登录会话失效」（401 被后端拒绝）：返回取消订阅函数 */
export function onAuthExpired(cb: () => void): () => void {
  authExpiredListeners.add(cb);
  return () => {
    authExpiredListeners.delete(cb);
  };
}

// ---- 离线缓存状态 ----
// Service Worker 断网兜底返回 IndexedDB 缓存时会带 X-StudyOS-Offline: 1 头，
// 这里统一感知并广播，供布局层显示「离线模式」横幅。
let offlineServed = false;
const offlineListeners = new Set<(offline: boolean) => void>();
export const isOfflineServed = () => offlineServed;
/** 订阅离线/在线状态切换（缓存兜底 ↔ 直连后端），返回取消订阅函数 */
export function onOfflineChange(cb: (offline: boolean) => void): () => void {
  offlineListeners.add(cb);
  return () => {
    offlineListeners.delete(cb);
  };
}
function noteOfflineServed(offline: boolean) {
  if (offlineServed !== offline) {
    offlineServed = offline;
    offlineListeners.forEach((cb) => cb(offline));
  }
}

/** 网络层失败（后端未启动/断网/跨源被拦）——fetch 只会以 TypeError 形式抛出。
 *  与 HTTP 错误（4xx/5xx，响应已回来）区分开，便于 UI 给出「后端未连接」的明确指引。 */
export class ApiNetworkError extends Error {
  constructor(path: string) {
    super(
      `无法连接后端 API（${BASE}${path}）：请确认已运行 ./start.sh 启动后端 ` +
        `（http://127.0.0.1:8800），然后刷新页面重试`,
    );
    this.name = "ApiNetworkError";
  }
}

/** HTTP 错误（4xx/5xx）：携带状态码，便于调用方区分「可重试」与「参数错误」等场景。 */
export class ApiHttpError extends Error {
  status: number;
  /** Retry-After 响应头（秒）：登录/注册被限速锁定时由后端下发，用于倒计时展示；无则 null */
  retryAfter: number | null;
  constructor(status: number, detail: string, retryAfter: number | null = null) {
    super(detail);
    this.name = "ApiHttpError";
    this.status = status;
    this.retryAfter = retryAfter;
  }
}

async function res(path: string, init?: RequestInit): Promise<Response> {
  const headers = new Headers(init?.headers);
  if (authToken && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${authToken}`);
  }
  let r: Response;
  try {
    r = await fetch(BASE + path, { ...init, headers });
  } catch (e) {
    if (e instanceof TypeError) throw new ApiNetworkError(path);
    throw e;
  }
  if (r.status === 401 && !path.startsWith("/auth/")) {
    // 登录会话失效（或登录保护已开启）：清除令牌并广播，App 层切回登录页
    setAuthToken(null);
    authExpiredListeners.forEach((cb) => cb());
  }
  if (!r.ok) {
    let detail = r.statusText;
    try {
      const body = await r.json();
      const message = body.detail ?? body;
      detail = typeof message === "string" ? message
        : Array.isArray(message) ? message.map((issue) => {
          const location = Array.isArray(issue?.loc) ? issue.loc.join(".") : "";
          return `${location ? `${location}: ` : ""}${issue?.msg ?? JSON.stringify(issue)}`;
        }).join("；")
        : JSON.stringify(message);
    } catch {
      /* ignore */
    }
    // 429 锁定/限速时后端会带 Retry-After（秒），供前端倒计时展示
    const raw = r.headers.get("retry-after");
    const retryAfter = raw ? Number(raw) : NaN;
    throw new ApiHttpError(
      r.status,
      detail,
      Number.isFinite(retryAfter) && retryAfter > 0 ? Math.ceil(retryAfter) : null,
    );
  }
  noteOfflineServed(r.headers.get("x-studyos-offline") === "1");
  return r;
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await res(path, init);
  if (r.status === 204) return undefined as T;
  return r.json();
}

const json = (method: string, body?: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: body === undefined ? undefined : JSON.stringify(body),
});

// ---- 文件下载（导出 / 报告 Markdown / 备份）----
// 下载统一走 fetch + Blob + <a download>，而不是让 <a href="/api/..."> 直接导航：
// 1. 登录保护开启时，导航请求不会带 Authorization 头，只会把 401 的 JSON 显示成页面；
// 2. Service Worker 的 SPA 回退会把 /api/* 的导航也换成 index.html，用户点了「没有任何反应」；
// 3. 后端未启动 / 导出失败时能把错误抛给界面，而不是静默什么都不发生。

/** 把 Blob 存成文件（object URL + 临时 <a download>，用完即回收） */
export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** 从 Content-Disposition 解析服务端给的文件名（拿不到时回落 fallback） */
export function filenameFromResponse(r: Response, fallback: string): string {
  const cd = r.headers.get("Content-Disposition") ?? "";
  const m = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(cd);
  if (!m) return fallback;
  try {
    return decodeURIComponent(m[1].trim()) || fallback;
  } catch {
    return m[1].trim() || fallback;
  }
}

/** 下载接口返回的文件（导出 / Markdown 报告等），返回实际落盘的文件名。
 *  `path` 可以是 `exportUrl()` / `mdUrl()` 那样的完整地址（带 BASE 前缀），
 *  也可以是 `res()` 用的相对路径——这里统一归一化，避免出现 `/api/api/...`。
 *  失败时抛出 ApiNetworkError / ApiHttpError，由调用方展示原因。 */
export async function downloadFile(path: string, fallbackName: string): Promise<string> {
  const rel = path.startsWith(`${BASE}/`) ? path.slice(BASE.length) : path;
  const r = await res(rel);
  const blob = await r.blob();
  const name = filenameFromResponse(r, fallbackName);
  saveBlob(blob, name);
  return name;
}

// ---- 下载失败提示 ----
// 下载是「点一下就走」的动作，失败很容易被当成没反应（尤其后端没启动时）。
// 这里做全局广播：没有就地展示错误的下载入口，由布局层弹一条横幅说明原因。
let downloadError: string | null = null;
const downloadErrorListeners = new Set<(message: string | null) => void>();
export const getDownloadError = () => downloadError;
/** 订阅下载失败提示（null 表示清除），返回取消订阅函数 */
export function onDownloadError(cb: (message: string | null) => void): () => void {
  downloadErrorListeners.add(cb);
  return () => {
    downloadErrorListeners.delete(cb);
  };
}
/** 上报 / 清除下载失败提示 */
export function reportDownloadError(message: string | null): void {
  if (downloadError === message) return;
  downloadError = message;
  downloadErrorListeners.forEach((cb) => cb(message));
}

/** 从响应头读分页元信息（后端过滤后的真实总数，不是当前页条数） */
const pageMeta = (r: Response, fallback: number): PageMeta => ({
  total: Number(r.headers.get("X-Total-Count") ?? fallback),
  limit: Number(r.headers.get("X-Limit") ?? fallback),
  offset: Number(r.headers.get("X-Offset") ?? 0),
  hasMore: r.headers.get("X-Has-More") === "1",
  tagFilter: r.headers.get("X-Tag-Filter") === "memory" ? "memory" : "sql",
});

// ---- 智能入库：格式目录 ----
export const apiIngest = {
  /**
   * 支持的文件格式目录（分组 + accept 串 + OCR 可用性）。
   * 返回值一律交给 `normalizeCatalog` 兜底解析：接口失败时前端降级到硬编码列表。
   */
  formats: () => req<FormatCatalog>("/ingest/formats"),
};

// ---- 课程 ----
export const apiCourses = {
  list: () => req<Course[]>("/courses"),
  create: (b: Partial<Course>) => req<Course>("/courses", json("POST", b)),
  update: (id: number, b: Partial<Course>) => req<Course>(`/courses/${id}`, json("PATCH", b)),
  remove: (id: number) => req<void>(`/courses/${id}`, { method: "DELETE" }),
  /** 导入课表：解析预览（上传 .ics/.xlsx/.xls/.csv/课表图片，或粘贴 iCal/JSON 文本，只读不落库） */
  importPreview: async (opts: { file?: File; text?: string }): Promise<CourseImportPreview> => {
    if (opts.file) {
      const fd = new FormData();
      fd.append("file", opts.file);
      const r = await res("/courses/import/preview", { method: "POST", body: fd });
      return r.json();
    }
    return req<CourseImportPreview>("/courses/import/preview", json("POST", { text: opts.text ?? "" }));
  },
  /** 确认导入：新课程创建（自动配色/学期锚点），同名课程合并课表 */
  importApply: (entries: CourseImportEntry[], opts?: { semester?: string; start_date?: string | null }) =>
    req<CourseImportResult>("/courses/import", json("POST", {
      entries: entries.map((e) => ({
        name: e.name, code: e.code, teacher: e.teacher, location: e.location, slots: e.slots,
      })),
      semester: opts?.semester ?? "",
      start_date: opts?.start_date ?? null,
    })),
};

export interface MaterialListParams {
  course_id?: number;
  type?: string;
  /** 章节过滤（包含匹配，如「第3章」） */
  chapter?: string;
  /** 难度过滤：easy / medium / hard */
  difficulty?: string;
  /** 只看考试重点资料 */
  exam_focus?: boolean;
  tag?: string;
  tags?: string[];
  tag_mode?: "any" | "all";
  limit?: number;
  offset?: number;
}

const materialParams = (p?: MaterialListParams): URLSearchParams => {
  const sp = new URLSearchParams();
  if (p?.course_id != null) sp.set("course_id", String(p.course_id));
  if (p?.type) sp.set("type", p.type);
  if (p?.chapter) sp.set("chapter", p.chapter);
  if (p?.difficulty) sp.set("difficulty", p.difficulty);
  if (p?.exam_focus) sp.set("exam_focus", "true");
  if (p?.tag) sp.set("tag", p.tag);
  for (const t of p?.tags ?? []) sp.append("tags", t);
  if (p?.tag_mode) sp.set("tag_mode", p.tag_mode);
  if (p?.limit != null) sp.set("limit", String(p.limit));
  if (p?.offset != null) sp.set("offset", String(p.offset));
  return sp;
};

// ---- 资料 ----
export const apiMaterials = {
  /** 列表（过滤全部在后端 SQL 完成，分页元信息在响应头） */
  list: async (params?: MaterialListParams): Promise<MaterialPage> => {
    const qs = materialParams(params).toString();
    const r = await res(`/materials${qs ? `?${qs}` : ""}`);
    const items = (await r.json()) as Material[];
    return { items, meta: pageMeta(r, items.length) };
  },
  /** 全库标签聚合（标签云），受课程/类型过滤约束 */
  tags: (params?: { course_id?: number; type?: string; limit?: number }) => {
    const sp = new URLSearchParams();
    if (params?.course_id != null) sp.set("course_id", String(params.course_id));
    if (params?.type) sp.set("type", params.type);
    if (params?.limit != null) sp.set("limit", String(params.limit));
    const qs = sp.toString();
    return req<TagFacet[]>(`/materials/tags${qs ? `?${qs}` : ""}`);
  },
  /** 全库章节聚合（章节下拉，按章节号自然排序） */
  chapters: (params?: { course_id?: number; type?: string; limit?: number }) => {
    const sp = new URLSearchParams();
    if (params?.course_id != null) sp.set("course_id", String(params.course_id));
    if (params?.type) sp.set("type", params.type);
    if (params?.limit != null) sp.set("limit", String(params.limit));
    const qs = sp.toString();
    return req<ChapterFacet[]>(`/materials/chapters${qs ? `?${qs}` : ""}`);
  },
  patch: (id: number, b: Partial<Material>) => req<Material>(`/materials/${id}`, json("PATCH", b)),
  remove: (id: number) => req<void>(`/materials/${id}`, { method: "DELETE" }),
  // ---- 批量操作（大资料库整理：批量删除 / 改标签 / 换课程）----
  batchDelete: (ids: number[]) =>
    req<MaterialBatchResult>("/materials/batch/delete", json("POST", { ids })),
  /** mode: add 追加 / remove 移除 / set 覆盖（set 空数组 = 清空标签） */
  batchTags: (ids: number[], tags: string[], mode: "add" | "remove" | "set") =>
    req<MaterialBatchResult>("/materials/batch/tags", json("POST", { ids, tags, mode })),
  /** courseId 为 null 表示移到「未分类」 */
  batchCourse: (ids: number[], courseId: number | null) =>
    req<MaterialBatchResult>("/materials/batch/course", json("POST", { ids, course_id: courseId })),
  /** 批量设置归档字段：仅提交的非 null 字段生效（chapter="" / difficulty="" 表示清空） */
  batchFields: (
    ids: number[],
    fields: {
      chapter?: string | null;
      difficulty?: string | null;
      exam_focus?: boolean | null;
      type?: string | null;
    },
  ) => req<MaterialBatchResult>("/materials/batch/fields", json("POST", { ids, ...fields })),
  upload: async (file: File, courseHint?: string, signal?: AbortSignal): Promise<SuggestionBundle> => {
    const fd = new FormData();
    fd.append("file", file);
    if (courseHint) fd.append("course_hint", courseHint);
    const r = await res(`/materials/upload`, { method: "POST", body: fd, signal });
    return r.json();
  },
  /** 附件原文件地址（内嵌预览/下载用；登录保护开启时需带 Authorization，见 fetchFileBlob） */
  fileUrl: (id: number) => `${BASE}/materials/${id}/file`,
  /** 带登录令牌拉取附件为 Blob（兼容登录保护开启状态；PDF/PPTX 内嵌预览统一走这里） */
  fetchFileBlob: async (id: number): Promise<Blob> => {
    const r = await res(`/materials/${id}/file`);
    return r.blob();
  },
};

// ---- 任务 ----
export const apiTasks = {
  list: (params?: { done?: boolean; course_id?: number }) => {
    const sp = new URLSearchParams();
    if (params?.done != null) sp.set("done", String(params.done));
    if (params?.course_id != null) sp.set("course_id", String(params.course_id));
    const qs = sp.toString();
    return req<Task[]>(`/tasks${qs ? `?${qs}` : ""}`);
  },
  create: (b: Partial<Task>) => req<Task>("/tasks", json("POST", b)),
  patch: (id: number, b: Partial<Task>) => req<Task>(`/tasks/${id}`, json("PATCH", b)),
  remove: (id: number) => req<void>(`/tasks/${id}`, { method: "DELETE" }),
  upcoming: (hours = 168) => req<Task[]>(`/tasks/upcoming?hours=${hours}`),
  overdue: () => req<Task[]>("/tasks/overdue"),
};

// ---- 检索 ----
export interface SearchFilters {
  course_id?: number;
  type?: string;
  /** 章节过滤（包含匹配） */
  chapter?: string;
  /** 难度过滤：easy / medium / hard */
  difficulty?: string;
  /** 只看考试重点资料 */
  exam_focus?: boolean;
  tag?: string;
  tags?: string[];
  tag_mode?: "any" | "all";
  mode?: "fts" | "semantic" | "hybrid";
  limit?: number;
}

export const apiSearch = {
  query: (q: string, filters?: SearchFilters) => {
    const sp = new URLSearchParams({ q });
    if (filters?.course_id != null) sp.set("course_id", String(filters.course_id));
    if (filters?.type) sp.set("type", filters.type);
    if (filters?.chapter) sp.set("chapter", filters.chapter);
    if (filters?.difficulty) sp.set("difficulty", filters.difficulty);
    if (filters?.exam_focus) sp.set("exam_focus", "true");
    if (filters?.tag) sp.set("tag", filters.tag);
    for (const t of filters?.tags ?? []) sp.append("tags", t);
    if (filters?.tag_mode) sp.set("tag_mode", filters.tag_mode);
    if (filters?.mode) sp.set("mode", filters.mode);
    if (filters?.limit != null) sp.set("limit", String(filters.limit));
    return req<SearchResult>(`/search?${sp.toString()}`);
  },
  semanticStatus: () => req<SemanticStatus>("/search/semantic/status"),
};

// ---- AI Agent ----
async function sendAgentWrite<T>(write: AgentWrite): Promise<T> {
  try {
    if (write.actorId || write.deviceId) {
      const caps = await apiAgent.capabilities();
      if (write.actorId && write.actorId !== caps.actor_id) throw new Error("原请求属于其它账号，请使用原账号查询回执");
      if (write.deviceId && write.deviceId !== caps.device_id && write.path !== '/agent/apply') throw new Error("此提案属于另一台设备，请在当前设备重新预览");
    }
    const token=authToken;
    const response = await req<T>(write.path, {
      ...json("POST", write.body),
      headers: { "Content-Type": "application/json", "Idempotency-Key": write.key, "Authorization": token ? `Bearer ${token}` : "" },
    });
    finishWrite(write.key);
    return response;
  } catch (error) {
    if (error instanceof ApiHttpError && error.status >= 400 && error.status < 500 && ![401, 429].includes(error.status)) finishWrite(write.key);
    throw error;
  }
}

async function writeExclusive<T>(action:()=>Promise<T>):Promise<T> {
  if (typeof navigator!=='undefined' && navigator.locks) return navigator.locks.request('studyos-agent-write',action);
  return action();
}
async function agentWrite<T>(path: string, body: Record<string, unknown>): Promise<T> {
  return writeExclusive(async()=>{
  await apiAgent.capabilities();
  const pending = loadPendingWrite();
  const write = pending?.path === path && JSON.stringify(pending.body) === JSON.stringify(body)
    ? pending : beginWrite(path, body);
  return sendAgentWrite<T>(write);
  });
}

export const apiAgent = {
  capabilities: async () => {
    const token=authToken;
    const value = await req<AgentCapabilities>("/agent/capabilities");
    if(token!==authToken) throw new Error("账号已切换，请重新加载后提交");
    if (value.actor_id && value.device_id) setWriteIdentity({actorId:value.actor_id,deviceId:value.device_id});
    return value;
  },
  chat: (body: { message: string; course?: string; task_id?: number; plan_id?: number; new_due_at?: string; hours_per_day?: number; conversation_id?: string; turn_id?: string; expected_revision?: number }, signal?: AbortSignal) =>
    req<AgentReply>("/agent/chat", { ...json("POST", body), signal }),
  conversations: () => req<{id:string;revision:number;updated_at:string}[]>("/agent/conversations"),
  conversation: (id:string) => req<{id:string;revision:number;messages:{role:string;content:string}[]}>(`/agent/conversations/${id}`),
  replan: (planId:number,hours=3) => req<import('./types').AgentProposal>("/agent/review/replan",json("POST",{plan_id:planId,hours_per_day:hours})),
  submitQueued: async (key:string,body:Record<string,unknown>,actorId?:string) => writeExclusive(async()=>{
    const caps=await apiAgent.capabilities();
    if(actorId && actorId!==caps.actor_id) throw new Error("队列属于另一账号，请切换到原账号后继续");
    return sendAgentWrite<Record<string,unknown>>(beginWrite('/agent/apply',body,key));
  }),
  operations: () => req<AgentOperation[]>("/agent/operations"),
  execute: (planId: string) => agentWrite<Record<string, unknown>>(`/agent/plans/${planId}/apply`, {}),
  undo: (operationId: string) => agentWrite<Record<string, unknown>>(`/agent/operations/${operationId}/undo`, {}),
  receipt: async (key: string) => {
    const pending=loadPendingWrite();
    if(pending?.key===key && pending.actorId){
      const caps=await apiAgent.capabilities();
      if(caps.actor_id!==pending.actorId) throw new Error("原请求属于其它账号，请使用原账号查询回执");
    }
    const receipt = await req<Record<string, unknown>>(`/agent/receipts/${encodeURIComponent(key)}`);
    finishWrite(key);
    return receipt;
  },
  retryPending: () => writeExclusive(async()=>{
    const pending = loadPendingWrite();
    if (!pending) throw new Error("没有待确认请求");
    return sendAgentWrite<Record<string, unknown>>(pending);
  }),
  extract: (text: string, course_hint?: string, signal?: AbortSignal) =>
    req<ExtractResult>("/agent/extract", { ...json("POST", { text, course_hint }), signal }),
  apply: (b: {
    tasks: TaskSuggestion[];
    materials: MaterialSuggestion[];
    file_path?: string;
    file_name?: string;
    source?: string;
    text?: string;
  }) => agentWrite<ApplyResult>("/agent/apply", b),
};

// ---- AI 笔记提炼 ----
export const apiNotes = {
  /** 从文本/资料全文提炼重点或考点笔记（预览，不落库） */
  refine: (b: { material_id?: number; text?: string; mode?: string }) =>
    req<RefineResult>("/notes/refine", json("POST", b)),
  /**
   * 文件提炼：上传 PDF / Word / Excel / PPT / 图片 / 文本等 → 抽正文 → 提炼笔记（预览，不落库）。
   * 图片走离线 OCR，文档走资料入库同一套抽取器（格式按内容判定，扫描版 PDF 自动 OCR）。
   */
  refineFile: async (file: File, mode: string): Promise<FileRefineResult> => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("mode", mode);
    const r = await res("/notes/refine-file", { method: "POST", body: fd });
    return r.json();
  },
  /** 识图提炼：图片专用入口（等价于 refineFile 的图片通道，保留给老调用方） */
  refineImage: async (file: File, mode: string): Promise<ImageRefineResult> => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("mode", mode);
    const r = await res("/notes/refine-image", { method: "POST", body: fd });
    return r.json();
  },
  list: (params?: { material_id?: number; mode?: string; limit?: number }) => {
    const sp = new URLSearchParams();
    if (params?.material_id != null) sp.set("material_id", String(params.material_id));
    if (params?.mode) sp.set("mode", params.mode);
    if (params?.limit != null) sp.set("limit", String(params.limit));
    const qs = sp.toString();
    return req<Note[]>(`/notes${qs ? `?${qs}` : ""}`);
  },
  get: (id: number) => req<Note>(`/notes/${id}`),
  create: (b: {
    material_id?: number | null;
    title: string;
    mode: string;
    summary: string;
    points: { point: string; importance: string }[];
    engine: string;
  }) => req<Note>("/notes", json("POST", b)),
  patch: (id: number, b: Partial<Pick<Note, "title" | "mode" | "summary" | "points">>) =>
    req<Note>(`/notes/${id}`, json("PATCH", b)),
  remove: (id: number) => req<void>(`/notes/${id}`, { method: "DELETE" }),
  /** 单条/全部笔记 Markdown 导出（浏览器直接下载） */
  mdUrl: (id?: number) => (id != null ? `${BASE}/notes/${id}/md` : `${BASE}/notes/export/md`),
};

// ---- 错题本（错题归集） ----
export interface WrongBookListParams {
  course_id?: number;
  /** 章节过滤（包含匹配，如「第3章」） */
  chapter?: string;
  /** 难度过滤：easy / medium / hard */
  difficulty?: string;
  /** 状态过滤：open 待攻克 / done 已掌握 */
  status?: string;
  tag?: string;
  tags?: string[];
  tag_mode?: "any" | "all";
  q?: string;
  limit?: number;
  offset?: number;
}

export const apiWrongbook = {
  /** 列表（过滤全在后端完成，分页元信息在响应头，待攻克排前） */
  list: async (params?: WrongBookListParams): Promise<{ items: WrongQuestion[]; meta: PageMeta }> => {
    const sp = new URLSearchParams();
    if (params?.course_id != null) sp.set("course_id", String(params.course_id));
    if (params?.chapter) sp.set("chapter", params.chapter);
    if (params?.difficulty) sp.set("difficulty", params.difficulty);
    if (params?.status) sp.set("status", params.status);
    if (params?.tag) sp.set("tag", params.tag);
    for (const t of params?.tags ?? []) sp.append("tags", t);
    if (params?.tag_mode) sp.set("tag_mode", params.tag_mode);
    if (params?.q) sp.set("q", params.q);
    if (params?.limit != null) sp.set("limit", String(params.limit));
    if (params?.offset != null) sp.set("offset", String(params.offset));
    const qs = sp.toString();
    const r = await res(`/wrongbook${qs ? `?${qs}` : ""}`);
    const items = (await r.json()) as WrongQuestion[];
    return { items, meta: pageMeta(r, items.length) };
  },
  /** 总数/待攻克/已掌握 + 按课程分布 */
  stats: () => req<WrongBookStats>("/wrongbook/stats"),
  /** 章节 + 标签聚合（过滤下拉用） */
  facets: () => req<WrongBookFacets>("/wrongbook/facets"),
  /** AI 提炼错题：粘贴错题原文 → 结构化字段（不落库，供表单预填核对） */
  refine: (text: string, title_hint?: string) =>
    req<WrongRefineResult>("/wrongbook/refine", json("POST", { text, title_hint })),
  create: (b: {
    course_id?: number | null;
    title: string;
    question?: string;
    my_answer?: string;
    answer?: string;
    reason?: string;
    source?: string;
    chapter?: string;
    difficulty?: string;
    tags?: string[];
  }) => req<WrongQuestion>("/wrongbook", json("POST", b)),
  patch: (
    id: number,
    b: Partial<Pick<WrongQuestion, "course_id" | "title" | "question" | "my_answer" | "answer" | "reason" | "source" | "chapter" | "difficulty" | "tags" | "status">>,
  ) => req<WrongQuestion>(`/wrongbook/${id}`, json("PATCH", b)),
  remove: (id: number) => req<void>(`/wrongbook/${id}`, { method: "DELETE" }),
  /** 重练自评：ok=true 答对了（连续 2 次 → 已掌握）；false 又错了 */
  practice: (id: number, ok: boolean) =>
    req<WrongQuestion>(`/wrongbook/${id}/practice`, json("POST", { ok })),
  /** 重练队列：随机抽取待攻克错题 */
  queue: (params?: { course_id?: number; limit?: number }) => {
    const sp = new URLSearchParams();
    if (params?.course_id != null) sp.set("course_id", String(params.course_id));
    if (params?.limit != null) sp.set("limit", String(params.limit));
    const qs = sp.toString();
    return req<WrongQuestion[]>(`/wrongbook/practice${qs ? `?${qs}` : ""}`);
  },
  /** 单条/全部错题 Markdown 导出（浏览器直接下载） */
  mdUrl: (id?: number) =>
    id != null ? `${BASE}/wrongbook/${id}/md` : `${BASE}/wrongbook/export/md`,
};

// ---- AI 自测 / 抽背（基于笔记/资料自动出选择题与问答题） ----
export const apiQuiz = {
  /** 生成一组自测并落库：来源四选一（text 自由文本 / material_id / note_ids / course_id） */
  create: (b: {
    course_id?: number | null;
    material_id?: number | null;
    note_ids?: number[];
    text?: string;
    qtype?: string;
    n?: number;
    title_hint?: string;
  }) => req<Quiz>("/quiz", json("POST", b)),
  /** 历史自测组（课程过滤 + 分页元信息响应头） */
  list: async (params?: { course_id?: number; limit?: number; offset?: number }): Promise<{ items: Quiz[]; meta: PageMeta }> => {
    const sp = new URLSearchParams();
    if (params?.course_id != null) sp.set("course_id", String(params.course_id));
    if (params?.limit != null) sp.set("limit", String(params.limit));
    if (params?.offset != null) sp.set("offset", String(params.offset));
    const qs = sp.toString();
    const r = await res(`/quiz${qs ? `?${qs}` : ""}`);
    const items = (await r.json()) as Quiz[];
    return { items, meta: pageMeta(r, items.length) };
  },
  /** 组数/已答题目/选择题正确率/问答自评通过率 */
  stats: () => req<QuizStats>("/quiz/stats"),
  get: (id: number) => req<Quiz>(`/quiz/${id}`),
  /** 提交一题：choice 字母（服务端判分）/ qa 自评 ok + 回忆内容 */
  answer: (id: number, b: { index: number; choice?: string; answer_text?: string; ok?: boolean }) =>
    req<Quiz>(`/quiz/${id}/answer`, json("POST", b)),
  /** 清空作答记录，重做这组 */
  reset: (id: number) => req<Quiz>(`/quiz/${id}/reset`, { method: "POST" }),
  remove: (id: number) => req<void>(`/quiz/${id}`, { method: "DELETE" }),
  /** 单组/全部自测 Markdown 导出（浏览器直接下载） */
  mdUrl: (id?: number) => (id != null ? `${BASE}/quiz/${id}/md` : `${BASE}/quiz/export/md`),
};

// ---- 复习 ----
export const apiReview = {
  list: () => req<ReviewPlan[]>("/review"),
  generate: (examTaskIds: number[], hoursPerDay = 3) =>
    req<ReviewPlan>("/review/generate", json("POST", { exam_task_ids: examTaskIds, hours_per_day: hoursPerDay })),
  remove: (id: number) => req<void>(`/review/${id}`, { method: "DELETE" }),
  /** 全部资料复习状态总览（掌握程度 / 间隔 / 到期标记），可按课程过滤 */
  states: (courseId?: number) =>
    req<ReviewState[]>(`/review/states${courseId != null ? `?course_id=${courseId}` : ""}`),
  /** 间隔重复评分：again/hard/good/easy，返回更新后的状态 */
  rate: (materialId: number, rating: string) =>
    req<ReviewState>("/review/rate", json("POST", { material_id: materialId, rating })),
  /** 重置一份资料的复习状态（重新开始间隔重复） */
  resetState: (materialId: number) => req<void>(`/review/state/${materialId}`, { method: "DELETE" }),
};

// ---- 番茄钟 / 专注计时 ----
export interface FocusSessionIn {
  mode: "focus" | "short" | "long";
  planned_min: number;
  duration_min: number;
  completed: boolean;
  interrupt_count?: number;
  task_id?: number | null;
  course_id?: number | null;
  plan_id?: number | null;
  plan_date?: string;
  material_ids?: number[];
  note?: string;
  started_at: string;
}

export const apiFocus = {
  /** 专注/休息结束落库（自然完成、放弃、跳过都记录） */
  create: (b: FocusSessionIn) => req<FocusSession>("/focus/sessions", json("POST", b)),
  /** 专注时间线（倒序分页，X-Total-Count 为真实总数） */
  list: async (params?: { limit?: number; offset?: number }) => {
    const sp = new URLSearchParams();
    if (params?.limit != null) sp.set("limit", String(params.limit));
    if (params?.offset != null) sp.set("offset", String(params.offset));
    const qs = sp.toString();
    const r = await res(`/focus/sessions${qs ? `?${qs}` : ""}`);
    return {
      items: (await r.json()) as FocusSession[],
      total: Number(r.headers.get("X-Total-Count") ?? 0),
    };
  },
  remove: (id: number) => req<void>(`/focus/sessions/${id}`, { method: "DELETE" }),
  /** 统计聚合：今日 / 近 N 天 / 连续天数 / 累计 / 课程分布 / 复习块投入 */
  stats: (days = 7) => req<FocusStats>(`/focus/stats?days=${days}`),
  /** 旧版本地统计一次性迁移（成功后前端清除本地旧数据） */
  importLegacy: (days: { date: string; count: number; minutes: number }[]) =>
    req<{ imported: number; days: number }>("/focus/import-legacy", json("POST", { days })),
};

// ---- 学习进度预测 / 负载预警 ----
export const apiForecast = {
  get: (hoursPerDay = 3, days = 21) => {
    const sp = new URLSearchParams({
      hours_per_day: String(hoursPerDay),
      days: String(days),
    });
    return req<Forecast>(`/forecast?${sp.toString()}`);
  },
};

// ---- 学习报告 / 学期统计 ----
export const apiReport = {
  /** 学期维度的数据大图：指标 + 掌握度趋势 / 复习次数 / 错题攻克率时间线 */
  get: () => req<Report>("/report"),
  /** 同一份数据的 Markdown 报告下载 */
  mdUrl: `${BASE}/report/md`,
};

// ---- 导出（支持增量：只导出自上次导出以来的变更） ----
export type ExportKind = "tasks" | "materials" | "review";
export type ExportFmt = "ical" | "csv" | "md";

export const exportUrl = (
  kind: ExportKind,
  fmt: ExportFmt,
  opts?: { incremental?: boolean; since?: string },
) => {
  const sp = new URLSearchParams();
  if (opts?.incremental) sp.set("incremental", "true");
  if (opts?.since) sp.set("since", opts.since);
  const qs = sp.toString();
  return `${BASE}/export/${kind}/${fmt}${qs ? `?${qs}` : ""}`;
};

export const apiExport = {
  status: () => req<ExportStatus>("/export/status"),
  records: (params?: { kind?: ExportKind; fmt?: ExportFmt; limit?: number }) => {
    const sp = new URLSearchParams();
    if (params?.kind) sp.set("kind", params.kind);
    if (params?.fmt) sp.set("fmt", params.fmt);
    if (params?.limit != null) sp.set("limit", String(params.limit));
    const qs = sp.toString();
    return req<ExportRecord[]>(`/export/records${qs ? `?${qs}` : ""}`);
  },
  /** 清空导出历史 = 重置增量基线（下次增量导出退化为全量） */
  clearRecords: (kind?: ExportKind) =>
    req<{ deleted: number }>(
      `/export/records${kind ? `?kind=${kind}` : ""}`,
      { method: "DELETE" },
    ),
};

// ---- 系统 ----
export const apiMisc = {
  stats: () => req<Stats>("/stats"),
  health: () => req<{ status: string; llm: string; ocr: string }>("/health"),
  getSettings: () => req<Record<string, string>>("/settings"),
  setSetting: (key: string, value: string) =>
    req<{ ok: boolean }>("/settings", json("PUT", { key, value })),
};

// ---- Web Push（Service Worker push 订阅，页面关闭也能收到提醒） ----
export interface PushHealth {
  /** 最近一轮调度检查时间（ISO，可能为 null：尚未跑过/刚启动） */
  last_tick_at: string | null;
  last_tick: {
    sent: number;
    removed: number;
    skipped: number;
    lib_available: boolean;
  } | null;
  /** 连续发送失败次数（任一成功即清零） */
  consecutive_failures: number;
  last_error: string | null;
  /** 连续失败 ≥2 → 推送链路异常，前端应启用页面内兜底提醒 */
  degraded: boolean;
}

export interface PushInfo {
  /** 后端推送库（pywebpush）是否就绪 */
  supported: boolean;
  vapid_public_key: string;
  subscriptions: number;
  tick_seconds: number;
  ttl_seconds: number;
  health: PushHealth;
}

export type PushFailKind =
  | "expired"
  | "rejected"
  | "payload"
  | "rate"
  | "server"
  | "config"
  | "network"
  | "unknown";

export interface PushTestResult {
  sent: number;
  lib_available: boolean;
  results: { endpoint: string; ok: boolean; kind?: PushFailKind; error?: string }[];
}

export const apiPush = {
  info: () => req<PushInfo>("/push/info"),
  subscribe: (sub: PushSubscriptionJSON) =>
    req<{ ok: boolean; subscriptions: number }>("/push/subscribe", json("POST", sub)),
  unsubscribe: (endpoint: string) =>
    req<{ ok: boolean; subscriptions: number }>(
      "/push/subscribe",
      json("DELETE", { endpoint }),
    ),
  test: () => req<PushTestResult>("/push/test", { method: "POST" }),
};

// ---- AI 配置 ----
export interface AIEntry {
  value: string;
  source: "db" | "env" | "default";
  set: boolean;
}
export interface AIConfig {
  presets: Record<string, { label: string; llm_base_url: string; llm_model: string; embed_base_url: string; embed_model: string }>;
  config: Record<string, AIEntry>;
  status: { llm: string; llm_base_url: string; llm_model: string; semantic: string; semantic_key_set: boolean };
}
export interface AIProbe {
  ok: boolean;
  latency_ms: number;
  error?: string;
  reply?: string;
  dim?: number;
  model?: string;
}
export interface AITestResult {
  chat: AIProbe;
  embed: AIProbe;
  llm_available: boolean;
}
export const apiAI = {
  config: () => req<AIConfig>("/ai/config"),
  save: (b: {
    llm_base_url?: string; llm_api_key?: string; llm_model?: string;
    embed_base_url?: string; embed_api_key?: string; embed_model?: string;
    clear_llm_key?: boolean; clear_embed_key?: boolean;
  }) => req<AIConfig>("/ai/config", json("PUT", b)),
  test: (b: {
    use_saved?: boolean;
    llm_base_url?: string; llm_api_key?: string; llm_model?: string;
    embed_base_url?: string; embed_api_key?: string; embed_model?: string;
  }) => req<AITestResult>("/ai/test", json("POST", b)),
};

// ---- 清单 / 模版库 / 分享链接 ----
export const apiChecklists = {
  list: (params?: { course_id?: number; status?: "all" | "open" | "done" }) => {
    const sp = new URLSearchParams();
    if (params?.course_id != null) sp.set("course_id", String(params.course_id));
    if (params?.status) sp.set("status", params.status);
    const qs = sp.toString();
    return req<Checklist[]>(`/checklists${qs ? `?${qs}` : ""}`);
  },
  get: (id: number) => req<Checklist>(`/checklists/${id}`),
  create: (b: {
    course_id?: number | null;
    title: string;
    description?: string;
    tags?: string[];
    due_at?: string | null;
    items?: { text: string; done?: boolean; note?: string }[];
  }) => req<Checklist>("/checklists", json("POST", b)),
  patch: (
    id: number,
    b: Partial<Pick<Checklist, "course_id" | "title" | "description" | "tags" | "due_at" | "items">>,
  ) => req<Checklist>(`/checklists/${id}`, json("PATCH", b)),
  remove: (id: number) => req<void>(`/checklists/${id}`, { method: "DELETE" }),
  /** 单条清单 Markdown 下载 */
  mdUrl: (id: number) => `${BASE}/checklists/${id}/md`,
  /** 一键导入分享清单：url = 分享链接（服务端抓取） */
  importFromUrl: (url: string) => req<Checklist>("/checklists/import", json("POST", { url })),
  /** 导入已下载的分享 JSON */
  importPayload: (payload: unknown) =>
    req<Checklist>("/checklists/import", json("POST", { payload })),
  /** 把清单存为模版（进模版库） */
  saveAsTemplate: (id: number, b?: { title?: string; category?: string }) =>
    req<ChecklistTemplate>(`/checklists/${id}/template`, json("POST", b ?? {})),
};

export const apiTemplates = {
  list: () => req<ChecklistTemplate[]>("/checklist-templates"),
  categories: () => req<string[]>("/checklist-templates/categories"),
  create: (b: {
    title: string;
    description?: string;
    category?: string;
    tags?: string[];
    items?: { text: string; note?: string }[];
  }) => req<ChecklistTemplate>("/checklist-templates", json("POST", b)),
  patch: (
    id: number,
    b: Partial<Pick<ChecklistTemplate, "title" | "description" | "category" | "tags" | "items">>,
  ) => req<ChecklistTemplate>(`/checklist-templates/${id}`, json("PATCH", b)),
  remove: (id: number) => req<void>(`/checklist-templates/${id}`, { method: "DELETE" }),
};

export const apiShares = {
  /** 为清单生成分享链接（expiresH 为 null = 永久，否则按小时过期） */
  create: (checklistId: number, expiresH: number | null) =>
    req<ShareInfo>(`/checklists/${checklistId}/share`, json("POST", { expires_h: expiresH })),
  /** 某条清单的全部分享链接 */
  forChecklist: (checklistId: number) => req<ShareInfo[]>(`/checklists/${checklistId}/shares`),
  /** 全部分享链接（管理/撤销） */
  list: () => req<ShareInfo[]>("/shares"),
  revoke: (token: string) => req<{ ok: boolean }>(`/shares/${token}`, { method: "DELETE" }),
};

/** 公开只读分享视图（跨源 GET，无需登录） */
export const apiShare = {
  view: (token: string) => req<SharedChecklist>(`/share/${token}`),
};

// ---- 多端同步 ----
export interface SyncCounts { created: number; updated: number; deleted: number; skipped: number; files_written: number; files_skipped: number; settings_updated: number }
export interface SyncStatus {
  device_id: string;
  /** 令牌掩码（只露末 4 位）——明文需显式调用 revealToken() */
  token_masked: string;
  /** 令牌指纹（sha256 前 8 位）：两端核对是否同一令牌，不可反推明文 */
  token_fingerprint: string;
  token_length: number;
  /** env = SYNC_TOKEN 环境变量固定；device = 本机随机生成 */
  token_source: "env" | "device";
  /** 当前来源是否允许查看明文（默认仅本机 loopback） */
  token_reveal_allowed: boolean;
  client_local: boolean;
  remote_admin_allowed: boolean;
  token_warning: string;
  remote_url: string;
  remote_token_set: boolean;
  counts: { courses: number; materials: number; tasks: number; reviews: number; tombstones: number };
  now: string;
}
export interface RevealedToken {
  token: string;
  fingerprint: string;
  warning: string;
  revealed_at: string;
}
export interface SyncResult {
  pushed: SyncCounts;
  pulled: SyncCounts;
  remote_url: string;
  /** v3 增量同步：true = 本轮为增量交换（按 updated_at/墓碑过滤 + 附件按哈希去重） */
  incremental?: boolean;
  /** 实际完成的往返轮数（对端身份变化 / 缺文件补齐时会自动补一轮） */
  rounds?: number;
}
export const apiSync = {
  status: () => req<SyncStatus>("/sync/status"),
  /** 显式披露明文令牌（配对用）：后端仅允许本机调用并记录披露 */
  revealToken: () => req<RevealedToken>("/sync/token/reveal", { method: "POST" }),
  regenerateToken: () =>
    req<{ token_masked: string; token_fingerprint: string; warning: string }>(
      "/sync/token/regenerate",
      { method: "POST" },
    ),
  now: async (remoteUrl: string, remoteToken: string, includeFiles = true) => {
    // 触发「把本端全量数据推给远端」需要本机令牌（与备份/恢复一致）：
    // 防止局域网内其它设备调用该接口把数据外带到任意地址
    const t = (await apiSync.revealToken()).token;
    return req<SyncResult>("/sync/now", {
      ...json("POST", { remote_url: remoteUrl, remote_token: remoteToken, include_files: includeFiles }),
      headers: { "Content-Type": "application/json", "X-Sync-Token": t },
    });
  },
  clearRemote: () => req<{ ok: boolean }>("/sync/remote/clear", { method: "POST" }),
  /** 备份/恢复与 snapshot、exchange 一样受 X-Sync-Token 保护：先经本机披露接口
   *  取明文令牌（后端会记录本次披露），随请求头一并发送。 */
  backup: async (): Promise<void> => {
    const r = await res(`/sync/backup`, {
      headers: { "X-Sync-Token": (await apiSync.revealToken()).token },
    });
    saveBlob(await r.blob(), filenameFromResponse(r, "studyos-backup.zip"));
  },
  restore: async (file: File): Promise<{ ok: boolean; stats: SyncCounts }> => {
    const fd = new FormData();
    fd.append("file", file);
    const r = await res(`/sync/restore`, {
      method: "POST",
      headers: { "X-Sync-Token": (await apiSync.revealToken()).token },
      body: fd,
    });
    return r.json();
  },
};

// ---- 本地账号 / 登录保护 ----
export interface AuthUser {
  id: number;
  username: string;
  created_at?: string;
  last_login_at?: string | null;
}
export interface AuthStatus {
  /** 登录保护是否开启（默认关闭 = 免登录） */
  enabled: boolean;
  /** 当前会话用户（未登录为 null） */
  user: AuthUser | null;
  /** 账号列表（保护开启且未登录时为 null，不泄露账号名） */
  users: AuthUser[] | null;
}
export interface AuthSessionInfo {
  /** 令牌 sha256（不可反推明文），也是撤销接口的 id */
  id: string;
  created_at: string;
  last_seen_at: string | null;
  expires_at: string | null;
  /** 是否为当前浏览器正在使用的会话 */
  current: boolean;
}
export interface AuthEventItem {
  id: number;
  kind: string;
  username: string;
  source_ip: string;
  detail: string;
  created_at: string;
}
export type ContactKind = "phone" | "wechat" | "qq";
export interface AuthContact {
  kind: ContactKind;
  label: string;
  bound: boolean;
  value: string | null;
  masked: string;
  verified_at: string | null;
}
export interface AuthVerificationChallenge {
  challenge_id: string;
  kind: ContactKind;
  masked: string;
  expires_in: number;
  delivery: "local" | string;
  /** 本机模式下用于完成验证；接入真实发送器后可不返回。 */
  dev_code?: string;
}
export interface AuthRegistrationContacts {
  phone?: { value: string; challenge_id: string; code: string };
  wechat?: { value: string; challenge_id: string; code: string };
  qq?: { value: string; challenge_id: string; code: string };
}
export const apiAuth = {
  status: async () => {
    const token=authToken;
    const value=await req<AuthStatus>("/auth/status");
    if(token!==authToken) throw new Error("账号已切换，请重新读取登录状态");
    setWriteIdentity({actorId:value.user?"user:"+value.user.username.toLowerCase():"local",deviceId:""});
    try {localStorage.setItem(AUTH_PROTECTION_KEY,value.enabled?"1":"0");}catch{ /* current runtime protection still applies */ }
    return value;
  },
  login: (username: string, password: string) =>
    req<{ token: string; user: AuthUser }>("/auth/login", json("POST", { username, password })),
  /** 保护未开启时注册即自动登录（返回 token）；开启后需已登录（管理员添加成员，token 为 null） */
  register: (
    username: string,
    password: string,
    password_confirm?: string,
    contacts?: AuthRegistrationContacts,
  ) =>
    req<{ ok: boolean; user: AuthUser; token: string | null }>(
      "/auth/register",
      json("POST", {
        username,
        password,
        ...(password_confirm === undefined ? {} : { password_confirm }),
        ...(contacts?.phone
          ? {
              phone: contacts.phone.value,
              phone_challenge_id: contacts.phone.challenge_id,
              phone_code: contacts.phone.code,
            }
          : {}),
        ...(contacts?.wechat
          ? {
              wechat: contacts.wechat.value,
              wechat_challenge_id: contacts.wechat.challenge_id,
              wechat_code: contacts.wechat.code,
            }
          : {}),
        ...(contacts?.qq
          ? {
              qq: contacts.qq.value,
              qq_challenge_id: contacts.qq.challenge_id,
              qq_code: contacts.qq.code,
            }
          : {}),
      }),
    ),
  requestVerification: (kind: ContactKind, value: string, purpose: "register" | "bind") =>
    req<AuthVerificationChallenge>(
      "/auth/verification/request",
      json("POST", { kind, value, purpose }),
    ),
  contacts: () => req<AuthContact[]>("/auth/contacts"),
  bindContact: (
    kind: ContactKind,
    value: string,
    challenge_id: string,
    code: string,
    current_password: string,
  ) =>
    req<{ ok: boolean; contacts: AuthContact[] }>(
      `/auth/contacts/${kind}`,
      json("PUT", { value, challenge_id, code, current_password }),
    ),
  unbindContact: (kind: ContactKind, current_password: string) =>
    req<{ ok: boolean; contacts: AuthContact[] }>(
      `/auth/contacts/${kind}`,
      json("DELETE", { current_password }),
    ),
  logout: () => req<{ ok: boolean }>("/auth/logout", { method: "POST" }),
  setEnabled: (enabled: boolean) =>
    req<{ ok: boolean; enabled: boolean }>("/auth/enable", json("POST", { enabled })),
  changePassword: (current_password: string, new_password: string) =>
    req<{ ok: boolean }>("/auth/password", json("POST", { current_password, new_password })),
  listUsers: () => req<AuthUser[]>("/auth/users"),
  removeUser: (id: number) => req<{ ok: boolean }>(`/auth/users/${id}`, { method: "DELETE" }),
  /** 当前账号的全部登录会话（最近活跃在前） */
  sessions: () => req<AuthSessionInfo[]>("/auth/sessions"),
  /** 撤销指定会话（撤销当前会话 = 立即登出） */
  revokeSession: (id: string) => req<{ ok: boolean }>(`/auth/sessions/${id}`, { method: "DELETE" }),
  /** 退出其它设备（撤销当前账号的其它全部会话） */
  revokeOtherSessions: () => req<{ ok: boolean }>("/auth/sessions/others", { method: "DELETE" }),
  /** 最近的安全事件（登录成败/注册/改密/开关保护/账号与会话管理） */
  events: (limit = 50) => req<AuthEventItem[]>(`/auth/events?limit=${limit}`),
};
