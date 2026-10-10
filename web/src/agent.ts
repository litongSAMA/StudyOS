import type { MaterialSuggestion, TaskSuggestion } from "./types";
import { acceptExtensions, fileExt, resolveExtAlias } from "./lib/formats.ts";

export const REVIEW_THRESHOLD = 0.85;
export const AGENT_TIMEOUT_MS = 120_000;
/** 兜底 accept：`GET /api/ingest/formats` 不可用时使用（等价于旧硬编码列表） */
export const DEFAULT_INGEST_ACCEPT = ".pdf,.ppt,.pptx,.doc,.docx,.txt,.md,.png,.jpg,.jpeg,.webp";
/**
 * 兼容旧引用：内容等同 DEFAULT_INGEST_ACCEPT。
 * 新代码请用 `DEFAULT_INGEST_ACCEPT` 或接口返回的 accept（`acceptFromCatalog`）。
 */
export const INGEST_ACCEPT = DEFAULT_INGEST_ACCEPT;
export type TaskDraft = TaskSuggestion & { keep: boolean; reviewed: boolean };
export type MaterialDraft = MaterialSuggestion & { keep: boolean; reviewed: boolean };

export function isDeadlineChangeNotice(value: string): boolean {
  return /延期|推迟|延后|改到|改为|调整到|提前到|延长至|截止时间.*(?:修改|调整)/.test(value);
}

export function needsReview(confidence: number): boolean {
  return !Number.isFinite(confidence) || confidence < REVIEW_THRESHOLD || confidence > 1;
}

export function toIso(value: string | null): string | null {
  if (!value?.trim()) return null;
  const parts = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:?\d{2})?$/.exec(value);
  if (!parts) return null;
  const [, year, month, day, hour, minute, second = "0"] = parts;
  const daysInMonth = new Date(Date.UTC(Number(year), Number(month), 0)).getUTCDate();
  if (Number(month) < 1 || Number(month) > 12 || Number(day) < 1 || Number(day) > daysInMonth || Number(hour) > 23 || Number(minute) > 59 || Number(second) > 59) return null;
  const date = new Date(value);
  // DST gaps must not silently shift a user's wall-clock deadline forward.
  if (!/(?:Z|[+-]\d{2}:?\d{2})$/.test(value) && (
    date.getFullYear() !== Number(year) || date.getMonth() + 1 !== Number(month) ||
    date.getDate() !== Number(day) || date.getHours() !== Number(hour) || date.getMinutes() !== Number(minute)
  )) return null;
  return Number.isFinite(date.getTime()) ? date.toISOString() : null;
}

export function toLocalInput(value: string | null): string {
  const iso = toIso(value);
  if (!iso) return "";
  const date = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function parseTags(value: string): string[] {
  return Array.from(new Set(value.split(/[，,;；\n]/).map((tag) => tag.trim()).filter(Boolean)));
}

// Only replace the target row: editing tags must never alter other suggestions.
export function updateMaterialTags<T extends MaterialSuggestion>(rows: T[], index: number, value: string): T[] {
  return rows.map((row, i) => i === index ? { ...row, tags: parseTags(value) } : row);
}

/**
 * 上传前校验：`accept` 由 `GET /api/ingest/formats` 动态提供（缺省用兜底列表）。
 * - 空文件直接拦下；
 * - 没有扩展名的文件放行，交给后端按内容嗅探（魔数识别比扩展名可靠）；
 * - accept 为空/非法时不做格式拦截，同样交给后端判定，避免误伤上传。
 */
export function validateIngestFile(file: Pick<File, "name" | "size">, accept: string = INGEST_ACCEPT): string {
  if (file.size === 0) return "文件为空，请选择有内容的文件";
  const allowed = acceptExtensions(accept);
  if (!allowed.length) return "";
  // 别名先归并：`.tgz` 与 `.tar.gz` 是同一个格式，accept 里只登记规范写法
  const extension = resolveExtAlias(fileExt(file.name));
  if (!extension) return "";
  if (!allowed.includes(extension)) {
    return "不支持此文件格式，请点「查看支持的格式」选择可解析的类型（或先转成 PDF/TXT）";
  }
  return "";
}

export function prepareAgentApply(tasks: TaskDraft[], materials: MaterialDraft[]) {
  const selectedTasks = tasks.filter((row) => row.keep);
  const selectedMaterials = materials.filter((row) => row.keep);
  if (!selectedTasks.length && !selectedMaterials.length) return { error: "请至少勾选一条任务或一份资料" } as const;
  for (const row of [...selectedTasks, ...selectedMaterials]) {
    if (!row.title.trim()) return { error: "选中条目的标题不能为空，请补充或取消勾选" } as const;
    if (needsReview(row.confidence) && !row.reviewed) return { error: `「${row.title}」置信度较低，请核对并勾选「已核对」` } as const;
  }
  for (const row of selectedTasks) {
    if (!toIso(row.due_at)) return { error: `「${row.title}」缺少有效截止时间，请补充或取消勾选` } as const;
  }
  const cleanTasks = selectedTasks.map(({ keep, reviewed, ...row }) => ({
    ...row, title: row.title.trim(), course: row.course.trim(), due_at: toIso(row.due_at),
  }));
  const cleanMaterials = selectedMaterials.map(({ keep, reviewed, ...row }) => ({
    ...row, title: row.title.trim(), course: row.course.trim(), chapter: row.chapter.trim(),
    tags: Array.from(new Set(row.tags.map((tag) => tag.trim()).filter(Boolean))),
  }));
  // Exact duplicates within this batch are blocked; different courses/deadlines are distinct.
  const taskKeys = cleanTasks.map((row) => JSON.stringify([row.title, row.course, row.kind, row.due_at]));
  const materialKeys = cleanMaterials.map((row) => JSON.stringify([row.title, row.course, row.type, row.chapter]));
  if (new Set(taskKeys).size !== taskKeys.length || new Set(materialKeys).size !== materialKeys.length) {
    return { error: "选中条目存在重复任务或资料，请取消重复项，或修改标题、课程等信息加以区分" } as const;
  }
  return { payload: { tasks: cleanTasks, materials: cleanMaterials } } as const;
}
