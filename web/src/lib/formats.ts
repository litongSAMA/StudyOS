/**
 * 格式目录的纯逻辑层（可单测，无 React / 无副作用）。
 *
 * 负责三件事：
 * 1. **兜底目录**：`DEFAULT_FORMAT_GROUPS` 等价于改造前的硬编码 accept，
 *    `GET /api/ingest/formats` 失败时上传区照常可用（静默降级）。
 * 2. **归一化**：接口返回的 JSON 一律当「不可信输入」解析——缺字段、空 groups、
 *    未知 group、重复扩展名、脏数据都收敛成可渲染的 `FormatCatalog`，不抛异常。
 * 3. **展示派生**：`acceptFor` / `acceptFromCatalog` / `summaryText` / `formatBadge`
 *    统一产出 accept 串、一句话摘要与格式徽标（含 mismatch 提示），
 *    避免「支持格式」文案散落在组件里各自漂移。
 */
import type { DetectedFormat, FormatCatalog, FormatGroup, FormatInfo } from "../types";

/** 分组顺序（与后端 ingest/formats.py 的 GROUP_ORDER 一致） */
export const GROUP_ORDER: readonly string[] = [
  "document",
  "slide",
  "sheet",
  "ebook",
  "notebook",
  "markup",
  "data",
  "code",
  "subtitle",
  "image",
  "archive",
  "media",
];

export const GROUP_LABELS: Record<string, string> = {
  document: "文档",
  slide: "演示文稿",
  sheet: "表格",
  ebook: "电子书",
  notebook: "代码笔记本",
  markup: "网页与标记",
  data: "结构化数据",
  code: "代码与脚本",
  subtitle: "字幕与听记",
  image: "图片（OCR 识别）",
  archive: "压缩包",
  media: "音视频",
  other: "其他",
};

/** 摘要里用的短名（「演示文稿」→「演示」），与上传区一句话提示对齐 */
const SHORT_GROUP_LABELS: Record<string, string> = {
  document: "文档",
  slide: "演示",
  sheet: "表格",
  ebook: "电子书",
  notebook: "代码笔记本",
  markup: "网页",
  data: "数据",
  code: "代码",
  subtitle: "字幕",
  image: "图片 OCR",
  archive: "压缩包",
  media: "音视频",
};

/** 摘要里优先展示的分组（其余分组只在后面加「等」） */
const SUMMARY_GROUPS: readonly string[] = [
  "document",
  "sheet",
  "slide",
  "ebook",
  "code",
  "subtitle",
  "image",
];

const MAX_SUMMARY_CATEGORIES = 7;

export const DEFAULT_UNKNOWN_NOTE = "未识别的文件类型，可转成 PDF/TXT 后重试";
export const UNSUPPORTED_NOTE = "本机暂不支持解析此格式，可先转成 PDF/TXT 后重试";
/** 压缩包的「不支持」提示：指引是「先解压」而不是「转 PDF」（7z/rar/dmg/zst） */
export const UNSUPPORTED_ARCHIVE_NOTE =
  "本机暂不支持解包此格式：请先用 7-Zip / WinRAR 等工具解压，再上传包内的课件/讲义";

/** 单条兜底格式（`supported=false` 用于 7z/rar 这类本机解不开的类型） */
function fallback(
  ext: string,
  label: string,
  group: string,
  note = "",
  ocr = false,
  supported = true,
): FormatInfo {
  return { ext, label, supported, ocr, note, group };
}

/**
 * 接口不可用时的兜底目录：与改造前的硬编码 accept 完全等价
 * （PDF / Office / TXT / Markdown / 图片），保证降级后上传功能不缩水。
 */
export const DEFAULT_FORMAT_GROUPS: FormatGroup[] = [
  {
    id: "document",
    label: GROUP_LABELS.document,
    formats: [
      fallback(".pdf", "PDF 文档", "document", "扫描版自动转 OCR", true),
      fallback(".doc", "Word 文档（旧版）", "document"),
      fallback(".docx", "Word 文档", "document"),
      fallback(".txt", "纯文本", "document"),
      fallback(".md", "Markdown", "document"),
    ],
  },
  {
    id: "slide",
    label: GROUP_LABELS.slide,
    formats: [fallback(".ppt", "PPT 演示（旧版）", "slide"), fallback(".pptx", "PPT 演示", "slide")],
  },
  {
    id: "image",
    label: GROUP_LABELS.image,
    formats: [
      fallback(".png", "PNG 图片", "image", "离线 OCR 识别", true),
      fallback(".jpg", "JPG 图片", "image", "离线 OCR 识别", true),
      fallback(".jpeg", "JPEG 图片", "image", "离线 OCR 识别", true),
      fallback(".webp", "WebP 图片", "image", "离线 OCR 识别", true),
    ],
  },
];

const str = (value: unknown): string => (typeof value === "string" ? value.trim() : "");

/** 扩展名归一化：小写、补前导点、非扩展名返回 ""（脏数据直接丢弃） */
export function normalizeExt(value: unknown): string {
  const raw = str(value).toLowerCase();
  if (!raw) return "";
  const ext = raw.startsWith(".") ? raw : `.${raw}`;
  return /^\.[a-z0-9][a-z0-9+._-]*$/.test(ext) ? ext : "";
}

/** accept 串 → 扩展名数组（去重、去空白、大小写归一、丢弃非法项） */
export function acceptExtensions(accept: unknown): string[] {
  if (typeof accept !== "string") return [];
  const seen = new Set<string>();
  const out: string[] = [];
  for (const part of accept.split(",")) {
    const ext = normalizeExt(part);
    if (ext && !seen.has(ext)) {
      seen.add(ext);
      out.push(ext);
    }
  }
  return out;
}

/**
 * 别名表（别名 → 规范扩展名），以后端下发的为准，接口不可用时用下面这份兜底。
 *
 * 后端登记表是唯一事实来源：`.tgz`/`.tbz2` 与 `.tar.gz`/`.tar.bz2` 是同一个格式，
 * 目录里只登记规范写法，靠这张表把别名并回去（与后端 `formats.ALIASES` 同源）。
 */
const BUILTIN_ALIASES: Record<string, string> = {
  ".tgz": ".tar.gz", ".taz": ".tar.gz",
  ".tbz": ".tar.bz2", ".tbz2": ".tar.bz2", ".tb2": ".tar.bz2",
  ".txz": ".tar.xz", ".tlz": ".tar.xz",
  ".jpe": ".jpg", ".jfif": ".jpg",
};

let aliasMap: Record<string, string> = { ...BUILTIN_ALIASES };

/** 接入接口下发的别名表（每次拉取格式目录后调用；缺字段时保持上一次的值） */
export function setAliasMap(raw: unknown): void {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return;
  const next: Record<string, string> = { ...BUILTIN_ALIASES };
  for (const [alias, canonical] of Object.entries(raw as Record<string, unknown>)) {
    const from = normalizeExt(alias);
    const to = normalizeExt(canonical);
    if (from && to) next[from] = to;
  }
  aliasMap = next;
}

/** 单个扩展名归并到规范写法（未登记别名时原样返回） */
export function resolveExtAlias(ext: unknown): string {
  const normalized = normalizeExt(ext);
  return normalized ? aliasMap[normalized] ?? normalized : "";
}

/** 两级扩展名（`.tar.gz`）是不是登记过的格式/别名写法 */
function isKnownExt(ext: string): boolean {
  return Boolean(ext) && (Boolean(aliasMap[ext]) || Object.values(aliasMap).includes(ext));
}

/**
 * 文件名 → 扩展名（用于上传前校验；无扩展名返回 ""，交给后端按内容嗅探）。
 *
 * 支持两段式扩展名：`资料.tar.gz` 要取出 `.tar.gz`（与 accept 里的写法一致），
 * 否则会被判成 `.gz`、与 accept 对不上而被前端误拦。判断依据是「后端登记过」，
 * 所以 `第1章.讲义.docx` 这类带点号的普通文件名不会被误判成两级扩展名。
 */
export function fileExt(name: unknown): string {
  const raw = str(name).toLowerCase();
  const dot = raw.lastIndexOf(".");
  if (dot <= 0 || dot === raw.length - 1) return "";
  const single = normalizeExt(raw.slice(dot));
  if (!single) return "";
  const prev = raw.lastIndexOf(".", dot - 1);
  if (prev > 0) {
    const compound = normalizeExt(raw.slice(prev));
    if (isKnownExt(compound)) return compound;
  }
  return single;
}

function normalizeFormats(raw: unknown, groupId: string, seenExt: Set<string>): FormatInfo[] {
  if (!Array.isArray(raw)) return [];
  const out: FormatInfo[] = [];
  for (const item of raw) {
    if (!item || typeof item !== "object") continue;
    const record = item as Record<string, unknown>;
    const ext = normalizeExt(record.ext);
    if (!ext || seenExt.has(ext)) continue;
    seenExt.add(ext);
    const label = str(record.label) || ext;
    const note = str(record.note);
    out.push({
      ext,
      label,
      supported: record.supported !== false,
      ocr: record.ocr === true,
      note,
      group: str(record.group) || groupId,
    });
  }
  return out;
}

function groupLabel(id: string, raw: unknown): string {
  return str(raw) || GROUP_LABELS[id] || id;
}

/** 归一化接口返回的分组列表：未知 group 保留并排到已知分组之后，空分组不渲染 */
export function normalizeGroups(raw: unknown): FormatGroup[] {
  if (!Array.isArray(raw)) return [];
  const seenExt = new Set<string>();
  const byId = new Map<string, FormatGroup>();
  for (const item of raw) {
    if (!item || typeof item !== "object") continue;
    const record = item as Record<string, unknown>;
    const id = str(record.id) || str(record.group) || "other";
    const formats = normalizeFormats(record.formats, id, seenExt);
    if (!formats.length) continue;
    const existing = byId.get(id);
    if (existing) existing.formats.push(...formats);
    else byId.set(id, { id, label: groupLabel(id, record.label), formats });
  }
  const order = new Map(GROUP_ORDER.map((id, index) => [id, index]));
  // 已知分组 → other → 完全未知的 group id（稳定排序，未知分组也能渲染出来）
  const rank = (id: string) =>
    order.get(id) ?? (id === "other" ? GROUP_ORDER.length : GROUP_ORDER.length + 1);
  return [...byId.values()].sort((a, b) => rank(a.id) - rank(b.id));
}

/** 兜底目录的深拷贝（避免调用方改动共享常量） */
function defaultGroups(): FormatGroup[] {
  return DEFAULT_FORMAT_GROUPS.map((group) => ({
    ...group,
    formats: group.formats.map((format) => ({ ...format })),
  }));
}

/** 可直接喂给 `<input accept>` 的扩展名串：只取 supported=true 的格式 */
export function acceptFor(groups: readonly FormatGroup[] | null | undefined): string {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const group of groups ?? []) {
    for (const format of group?.formats ?? []) {
      const ext = normalizeExt(format?.ext);
      if (!ext || seen.has(ext) || format?.supported === false) continue;
      seen.add(ext);
      out.push(ext);
    }
  }
  return out.join(",");
}

/**
 * 取 accept 串：优先用接口返回值（保持后端顺序），但必须剔除 supported=false 的格式
 * （HEIC 之类「认得出来但本机做不了」的类型不进 accept）；缺失时按目录派生，
 * 再兜底到硬编码列表——任何一步都不返回空串之外的意外结果。
 */
export function acceptFromCatalog(catalog: FormatCatalog | null | undefined): string {
  const groups = catalog?.groups ?? [];
  const supported = new Set(acceptExtensions(acceptFor(groups)));
  const fromApi = acceptExtensions(catalog?.accept).filter((ext) => supported.has(ext));
  if (fromApi.length) return fromApi.join(",");
  // 目录派生（接口没给 accept 时）：连「规范写法的别名」一起列进 `<input accept>`，
  // 文件选择框才能同时选中 .tar.gz 与 .tgz（校验时靠 resolveExtAlias 归并）。
  // 只加规范写法确实在目录里、且接口下发的别名，避免凭空多出没登记的格式。
  const derived = acceptExtensions(acceptFor(groups));
  const present = new Set(derived);
  const aliases = Object.keys(catalog?.aliases ?? {})
    .map((alias) => normalizeExt(alias))
    .filter((alias) => present.has(aliasMap[alias] ?? ""))
    .filter((alias, index, all) => all.indexOf(alias) === index);
  if (derived.length) return [...derived, ...aliases].join(",");
  return acceptFor(DEFAULT_FORMAT_GROUPS);
}

/** 课表导入支持的扩展名（`<input accept>`）：教务系统导出 + iCal/JSON + 课表截图 */
export const TIMETABLE_ACCEPT =
  ".ics,.ical,.icalendar,.ifb,.json,.xlsx,.xlsm,.xls,.ods,.csv,.tsv,.txt,.docx,.pdf,.html,.htm,.png,.jpg,.jpeg,.webp,.bmp,.gif,.tif,.tiff,.avif";

// ---------------------------------------------------------------------------
// 「AI 笔记提炼」可直传的格式（识图 + 文档）
// ---------------------------------------------------------------------------

/** 提炼面板排除的分组：音视频无法离线转写、other 是认不出的类型 */
export const REFINE_EXCLUDED_GROUPS: readonly string[] = ["media", "other"];

/** 提炼面板可直传的分组目录（剔空分组；顺序沿用 GROUP_ORDER） */
export function refineGroups(groups: readonly FormatGroup[] | null | undefined): FormatGroup[] {
  return (groups ?? [])
    .filter((group) => group && !REFINE_EXCLUDED_GROUPS.includes(group.id))
    .map((group) => ({
      ...group,
      formats: (group.formats ?? []).filter((format) => format?.supported !== false),
    }))
    .filter((group) => group.formats.length > 0);
}

/**
 * 提炼面板的 accept 串：优先用接口的 `refine_accept`（与后端可直传集合一致），
 * 但它必须落在 supported 的格式内；缺失时按目录派生（排除音视频/未知），
 * 再兜底到提炼专用兜底列表——任何一步都不会返回「能选却提炼不了」的扩展名。
 */
export function refineAcceptFromCatalog(catalog: FormatCatalog | null | undefined): string {
  const groups = refineGroups(catalog?.groups);
  const allowed = new Set(acceptExtensions(acceptFor(groups)));
  const fromApi = acceptExtensions(catalog?.refine_accept).filter((ext) => allowed.has(ext));
  if (fromApi.length) return fromApi.join(",");
  const derived = acceptFor(groups);
  if (derived) return derived;
  return refineAcceptFallback();
}

/** 提炼兜底目录里补上的格式：兜底目录偏「上传常用」，提炼还要覆盖表格/电子书等 */
export const REFINE_FALLBACK_FORMATS: FormatGroup[] = [
  {
    id: "sheet",
    label: GROUP_LABELS.sheet,
    formats: [
      fallback(".xlsx", "Excel 工作簿", "sheet"),
      fallback(".xlsm", "Excel 启用宏的工作簿", "sheet"),
      fallback(".xls", "Excel 97-2003 工作簿", "sheet"),
      fallback(".csv", "CSV 表格", "sheet"),
    ],
  },
  {
    id: "ebook",
    label: GROUP_LABELS.ebook,
    formats: [fallback(".epub", "EPUB 电子书", "ebook")],
  },
  {
    id: "archive",
    label: GROUP_LABELS.archive,
    formats: [
      fallback(".zip", "ZIP 压缩包", "archive", "自动抽取包内可解析的文档/文本"),
      fallback(".tar", "TAR 归档", "archive", "自动抽取包内可解析的文档/文本"),
      fallback(".tar.gz", "TAR.GZ 压缩包", "archive", "自动抽取包内文档（.tgz 同此格式）"),
      fallback(".gz", "GZIP 压缩文件", "archive", "解压后按真实内容抽取"),
      fallback(".rar", "RAR 压缩包", "archive", "请先用 WinRAR / 7-Zip 解压后上传包内文档", false, false),
    ],
  },
];

/**
 * 提炼面板的兜底目录：目录接口不可用时用它，保证「PDF / Word / Excel / PPT / 图片」
 * 这些主推能力不会因为一次请求失败就从界面上消失（`fallbackCatalog` 只管上传常用）。
 */
export function refineFallbackCatalog(): FormatCatalog {
  const base = fallbackCatalog();
  const byId = new Map(base.groups.map((group) => [group.id, { ...group, formats: [...group.formats] }]));
  for (const extra of REFINE_FALLBACK_FORMATS) {
    const group = byId.get(extra.id) ?? { id: extra.id, label: extra.label, formats: [] };
    for (const format of extra.formats) {
      if (!group.formats.some((item) => item.ext === format.ext)) group.formats.push({ ...format });
    }
    byId.set(extra.id, group);
  }
  return normalizeCatalog({ groups: [...byId.values()], unknown_note: base.unknown_note });
}

/** 目录接口不可用时的提炼 accept：PDF / Office / 表格 / 文本 / 图片 */
export function refineAcceptFallback(): string {
  return acceptFor(refineGroups(refineFallbackCatalog().groups));
}

/** 提炼面板的一句话摘要，例如 `支持 90 种格式 · 文档 / 表格 / 演示 / 图片 OCR` */
export function refineSummaryText(groups: readonly FormatGroup[] | null | undefined): string {
  const present = refineGroups(groups);
  if (!present.length) return "支持 PDF / Word / Excel / PPT / 图片等文件";
  return summaryText(present);
}

/** 该扩展名/文件名能否直接用于提炼（与后端 `refine_specs` 同一口径） */
export function isRefinableFile(
  name: unknown,
  catalog: FormatCatalog | null | undefined,
): boolean {
  const ext = resolveExtAlias(fileExt(name));
  const allowed = new Set(acceptExtensions(refineAcceptFromCatalog(catalog)));
  if (ext) return allowed.has(ext);
  // 无扩展名：交给后端按内容嗅探（拖入的无扩展名导出文件也能识别）
  return true;
}

/** 扩展名 → 格式目录里的条目（未登记返回 null） */
export function findFormat(
  catalog: FormatCatalog | null | undefined,
  ext: string,
): FormatInfo | null {
  const wanted = resolveExtAlias(ext);
  if (!wanted) return null;
  for (const group of catalog?.groups ?? []) {
    for (const format of group?.formats ?? []) {
      if (normalizeExt(format?.ext) === wanted) return format;
    }
  }
  return null;
}

/**
 * 提炼前的格式拦截理由：可提炼返回 ""，否则返回「为什么不行 + 怎么办」。
 *
 * 三类原因分开说，避免把「HEIC 缺解码器」说成「音视频不能转写」：
 * 本机缺解码器 → 用目录里的 note（先转 JPG/PNG）；音视频 → 无法离线转写；
 * 其余（未知 / 认得却抽不出）→ 先转 PDF/TXT 或直接粘贴文本。
 */
export function refineRejectReason(
  name: unknown,
  catalog: FormatCatalog | null | undefined,
): string {
  const ext = resolveExtAlias(fileExt(name));
  if (!ext || isRefinableFile(name, catalog)) return "";
  const format = findFormat(catalog, ext);
  const label = str(format?.label) || ext;
  // 音视频要先判：它们同样 supported=false，但原因是「不能转写」而不是「缺解码器」
  if (format && format.group === "media") {
    return `${label}无法离线转写（本机不联网、也没有语音识别）；可上传配套的课件/讲义，或把文字粘贴到左边「自由提炼」`;
  }
  if (format && format.supported === false) {
    return `${label}本机暂不能解析：${str(format.note) || UNSUPPORTED_NOTE}`;
  }
  const note = str(format?.note);
  return `暂不支持提炼 ${ext}（${label}）${
    note ? `：${note}` : "：可先转成 PDF / Word / TXT，或把文字粘贴到左边「自由提炼」"
  }`;
}

/** 未知类型时给用户的提示（接口可能没带 unknown_note） */
export function unknownNoteOf(catalog: FormatCatalog | null | undefined): string {
  return str(catalog?.unknown_note) || DEFAULT_UNKNOWN_NOTE;
}

/** 把任意接口回包收敛成可渲染的目录；groups 为空/字段缺失时降级到兜底目录 */
export function normalizeCatalog(raw: unknown): FormatCatalog {
  const record = raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {};
  setAliasMap(record.aliases);
  const groups = normalizeGroups(record.groups);
  const finalGroups = groups.length ? groups : defaultGroups();
  const extensions = acceptExtensions(record.extensions);
  const catalog: FormatCatalog = {
    groups: finalGroups,
    accept: "",
    extensions: extensions.length ? extensions : finalGroups.flatMap((g) => g.formats.map((f) => f.ext)),
    // 只有接口真的下发了别名表才带上：兜底目录不并入别名，
    // 保证「接口失败 → 兜底 accept」与改造前逐字一致
    aliases: "aliases" in record ? { ...aliasMap } : {},
    unknown_note: str(record.unknown_note) || DEFAULT_UNKNOWN_NOTE,
    ocr_available: record.ocr_available === true,
  };
  catalog.accept = acceptFromCatalog({ ...catalog, accept: str(record.accept) });
  catalog.refine_accept = refineAcceptFromCatalog({
    ...catalog,
    refine_accept: str(record.refine_accept),
  });
  return catalog;
}

/** 兜底目录对应的目录对象（接口失败时直接用，等价于旧硬编码列表） */
export function fallbackCatalog(): FormatCatalog {
  // 传 null：没有接口数据 → 不并入别名（兜底 accept 与改造前逐字一致）
  return normalizeCatalog(null);
}

/** 目录里的格式总数（用于「支持 N 种格式」） */
export function formatCount(groups: readonly FormatGroup[] | null | undefined): number {
  return (groups ?? []).reduce((sum, group) => sum + (group?.formats?.length ?? 0), 0);
}

function shortGroupLabel(group: FormatGroup): string {
  return SHORT_GROUP_LABELS[group.id] || str(group.label).replace(/[（(].*$/, "") || group.id;
}

const FALLBACK_SUMMARY = "支持 PDF / Word / PPT / TXT / Markdown / 图片等常见学习文件格式";

/**
 * 上传区一句话摘要，例如：
 * `支持 96 种格式 · 文档 / 表格 / 演示 / 电子书 / 代码 / 字幕 / 图片 OCR`
 */
export function summaryText(groups: readonly FormatGroup[] | null | undefined): string {
  const present = (groups ?? []).filter((group) => (group?.formats?.length ?? 0) > 0);
  const total = formatCount(present);
  if (!present.length || total === 0) return FALLBACK_SUMMARY;
  const byId = new Map(present.map((group) => [group.id, group]));
  const picked: FormatGroup[] = [];
  for (const id of SUMMARY_GROUPS) {
    const group = byId.get(id);
    if (group) picked.push(group);
  }
  for (const group of present) {
    if (picked.length >= MAX_SUMMARY_CATEGORIES) break;
    if (!picked.includes(group)) picked.push(group);
  }
  const shown = picked.slice(0, MAX_SUMMARY_CATEGORIES);
  const categories = shown.map(shortGroupLabel).join(" / ");
  const more = present.length > shown.length ? " 等" : "";
  return `支持 ${total} 种格式 · ${categories}${more}`;
}

export interface FormatBadge {
  /** 主文案（中文名，缺失时退化成扩展名，未知类型为「未知格式」） */
  label: string;
  ext: string;
  groupLabel: string;
  note: string;
  supported: boolean;
  mismatch: boolean;
  /** mismatch 时给用户的醒目提示，其余情况为空串 */
  mismatchNote: string;
  tone: "ok" | "muted" | "warn";
}

/** 扩展名与实际内容不一致时的提示（后端已按实际内容解析） */
export function mismatchNotice(format: Partial<DetectedFormat> | null | undefined): string {
  if (!format || format.mismatch !== true) return "";
  const declared = normalizeExt(format.declared_ext);
  const detected = normalizeExt(format.detected_ext);
  const detail = declared || detected ? `（声明 ${declared || "无扩展名"}，实际 ${detected || "未知"}）` : "";
  return `扩展名与实际内容不一致${detail}：后端已按实际内容解析，请核对下方结果`;
}

/**
 * 格式徽标：上传回包里的 `format` 可能为 null 或字段缺失，这里统一兜底。
 * `tone=warn` 表示 mismatch（按实际内容解析），`muted` 表示本机不支持抽取。
 */
export function formatBadge(
  format: Partial<DetectedFormat> | null | undefined,
  unknownNote: string = DEFAULT_UNKNOWN_NOTE,
): FormatBadge {
  if (!format) {
    return {
      label: "未知格式",
      ext: "",
      groupLabel: "",
      note: str(unknownNote) || DEFAULT_UNKNOWN_NOTE,
      supported: false,
      mismatch: false,
      mismatchNote: "",
      tone: "muted",
    };
  }
  const ext = normalizeExt(format.ext) || normalizeExt(format.detected_ext);
  const supported = format.supported !== false;
  const fallbackNote = str(format.group) === "archive" ? UNSUPPORTED_ARCHIVE_NOTE : UNSUPPORTED_NOTE;
  const note = str(format.note) || (supported ? "" : fallbackNote);
  const mismatch = format.mismatch === true;
  return {
    label: str(format.label) || ext || "未知格式",
    ext,
    groupLabel: str(format.group_label) || GROUP_LABELS[str(format.group)] || str(format.group),
    note,
    supported,
    mismatch,
    mismatchNote: mismatchNotice(format),
    tone: mismatch ? "warn" : supported ? "ok" : "muted",
  };
}

/** 抽取字数文案（缺省/非法值返回空串，由调用方决定是否渲染） */
export function textCharsText(value: unknown): string {
  const chars = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(chars) || chars <= 0) return "";
  return `${Math.round(chars)} 字`;
}

/** 课表预览的来源格式文案：优先中文名，缺失时退回 format 原值 */
export function importFormatLabel(preview: {
  format?: unknown;
  format_label?: unknown;
} | null | undefined): string {
  return str(preview?.format_label) || str(preview?.format) || "未知格式";
}
