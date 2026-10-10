import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  Check,
  Edit3,
  FileArchive,
  FileSpreadsheet,
  FileText,
  FileType2,
  ImagePlus,
  Presentation,
  RefreshCw,
  Save,
  ScanText,
  Trash2,
  Upload,
  Wand2,
  X,
} from "lucide-react";
import { apiIngest, apiNotes, ApiHttpError } from "../api";
import {
  fileExt,
  normalizeCatalog,
  refineAcceptFromCatalog,
  refineFallbackCatalog,
  refineGroups,
  refineRejectReason,
  refineSummaryText,
} from "../lib/formats";
import { IMPORTANCE_COLORS, IMPORTANCE_LABELS, NOTE_MODES } from "../types";
import type { FileRefineResult, FormatCatalog as FormatCatalogData, RefineResult } from "../types";
import FormatCatalog from "./FormatCatalog";
import { Spinner } from "./ui";

/** 图片上限（后端同一条线：OCR 前要整图解码） */
const MAX_IMAGE_BYTES = 20 * 1024 * 1024;
/** 文档上限（PDF / Word / Excel / PPT…，与后端 MAX_DOC_BYTES 一致） */
const MAX_DOC_BYTES = 50 * 1024 * 1024;

/** 图片扩展名（预览缩略图 / 体积口径 / 文案都用它） */
const IMAGE_EXTS = new Set([".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff", ".heic", ".heif", ".avif"]);

/** 扩展名 → 分组（仅用于选文件后挑图标；真实格式仍以后端嗅探结果为准） */
const EXT_GROUPS: Record<string, string> = {
  ".xlsx": "sheet", ".xlsm": "sheet", ".xls": "sheet", ".ods": "sheet",
  ".csv": "sheet", ".tsv": "sheet",
  ".pptx": "slide", ".ppt": "slide", ".odp": "slide",
  ".zip": "archive", ".tar": "archive", ".tar.gz": "archive", ".tgz": "archive",
  ".tar.bz2": "archive", ".tar.xz": "archive", ".tbz2": "archive", ".txz": "archive",
  ".gz": "archive", ".bz2": "archive", ".xz": "archive",
  ".7z": "archive", ".rar": "archive", ".dmg": "archive", ".zst": "archive",
};

/** 文件类型图标：按分组挑一个（纯展示，认不出用通用图标） */
function fileIcon(group: string) {
  if (group === "sheet") return FileSpreadsheet;
  if (group === "slide") return Presentation;
  if (group === "archive") return FileArchive;
  if (group === "image") return ImagePlus;
  if (group && group !== "document") return FileType2;
  return FileText;
}

/** 文件名 → 图标分组（后端嗅探结果优先，其次按扩展名猜） */
function iconGroupOf(name: string, detected = ""): string {
  if (detected) return detected;
  const ext = fileExt(name);
  if (IMAGE_EXTS.has(ext)) return "image";
  return EXT_GROUPS[ext] ?? "";
}

function humanSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

/**
 * 文件提炼面板：「AI 笔记提炼」页专用（识图 + 文档一条通道）。
 *
 * 功能：
 * - 上传图片（点击 / 拖拽 / Ctrl+V 粘贴截图）→ 离线 OCR；上传 PDF / Word / Excel /
 *   PPT / 文本等 → 走「资料入库」同一套抽取器（格式按内容判定，扫描件自动转 OCR）
 * - 抽取原文可展开核对，也可进入编辑模式修正错字后重新提炼
 * - 切换模式时用已有原文直接重新提炼，无需再次上传
 * - 抽取提示（扫描件 OCR / 扩展名与内容不一致 / 原文被截断）即时展示
 * - 可直传的格式由 `/api/ingest/formats` 的 `refine_accept` 派生，接口不可用时静默降级
 */
export default function FileRefinePanel({ onSaved }: { onSaved?: () => void }) {
  const [mode, setMode] = useState<"keypoints" | "exam">("keypoints");
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState("");
  const [dragOver, setDragOver] = useState(false);
  const [busy, setBusy] = useState(false);
  /** 当前处理阶段：extract=识别/解析原文中 / refine=AI 提炼中（前端估算，用于按钮文案） */
  const [stage, setStage] = useState<"extract" | "refine" | null>(null);
  const [result, setResult] = useState<FileRefineResult | null>(null);
  const [showText, setShowText] = useState(false);
  const [title, setTitle] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState("");
  // 抽取原文编辑状态（修正 OCR 错字 / 抽取噪声后重新提炼）
  const [editingText, setEditingText] = useState(false);
  const [draftText, setDraftText] = useState("");
  /** 503 模型加载中时的自动重试提示（空串=未在重试） */
  const [retryHint, setRetryHint] = useState("");
  // 格式目录：接口失败时用兜底列表（上传区照常可用）
  const [catalog, setCatalog] = useState<FormatCatalogData>(() => refineFallbackCatalog());
  const [catalogDegraded, setCatalogDegraded] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const stageTimer = useRef<number | null>(null);
  const retryTimer = useRef<number | null>(null);

  useEffect(() => {
    let alive = true;
    apiIngest
      .formats()
      .then((raw) => {
        if (!alive) return;
        setCatalog(normalizeCatalog(raw));
        setCatalogDegraded(false);
      })
      .catch(() => {
        if (!alive) return;
        setCatalog(refineFallbackCatalog());
        setCatalogDegraded(true);
      });
    return () => {
      alive = false;
    };
  }, []);

  // 卸载时回收预览对象 URL 与计时器
  useEffect(() => {
    return () => {
      if (preview) URL.revokeObjectURL(preview);
      if (stageTimer.current) window.clearTimeout(stageTimer.current);
      if (retryTimer.current) window.clearTimeout(retryTimer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const accept = useMemo(() => refineAcceptFromCatalog(catalog), [catalog]);
  const groups = useMemo(() => refineGroups(catalog.groups), [catalog]);
  const isImage = !!file && IMAGE_EXTS.has(fileExt(file.name));

  const pick = (f: File | undefined | null) => {
    setError("");
    setSaved(false);
    setEditingText(false);
    if (!f) return;
    const image = IMAGE_EXTS.has(fileExt(f.name));
    // 格式拦截：文案按原因区分（缺解码器 / 音视频 / 认不出），并给出下一步动作
    const reason = refineRejectReason(f.name, catalog);
    if (reason) {
      setError(reason);
      return;
    }
    const limit = image ? MAX_IMAGE_BYTES : MAX_DOC_BYTES;
    if (f.size > limit) {
      setError(`${image ? "图片" : "文件"}超过 ${limit / 1024 / 1024}MB，请压缩后重试`);
      return;
    }
    if (preview) URL.revokeObjectURL(preview);
    setPreview(image ? URL.createObjectURL(f) : "");
    setFile(f);
    setResult(null);
    setShowText(false);
  };

  const clearFile = () => {
    if (preview) URL.revokeObjectURL(preview);
    if (retryTimer.current) window.clearTimeout(retryTimer.current);
    setPreview("");
    setFile(null);
    setResult(null);
    setShowText(false);
    setError("");
    setRetryHint("");
    setEditingText(false);
    setDraftText("");
    if (inputRef.current) inputRef.current.value = "";
  };

  /** 重新提炼（文本通道）：只换提炼结果，保留抽取原文与格式元信息 */
  const applyResult = (r: RefineResult) => {
    setResult((prev) => ({ ...(prev as FileRefineResult), ...r }));
    setTitle(r.title);
    setSaved(false);
  };

  /** 首次：上传文件 → 抽取原文（图片 OCR / 文档解析）→ 提炼。503 自动延迟重试。 */
  const MAX_AUTO_RETRY = 3;
  const RETRY_DELAY_MS = 3000;
  const run = async (retries = 0) => {
    if (!file) return;
    setBusy(true);
    setStage("extract");
    setError("");
    setRetryHint("");
    setSaved(false);
    setEditingText(false);
    // 后端单次请求返回，前端按时间估算阶段：前 2 秒大概率在识别/解析，之后在 AI 提炼
    if (stageTimer.current) window.clearTimeout(stageTimer.current);
    stageTimer.current = window.setTimeout(() => setStage("refine"), 2000);
    let shouldRetry = false;
    try {
      const r = await apiNotes.refineFile(file, mode);
      setResult(r);
      setTitle(r.title);
    } catch (e) {
      // 503 = OCR 模型后台预热中，自动延迟重试，最多 MAX_AUTO_RETRY 次
      if (e instanceof ApiHttpError && e.status === 503 && retries < MAX_AUTO_RETRY) {
        shouldRetry = true;
      } else {
        setError((e as Error).message);
        setResult(null);
      }
    }
    if (stageTimer.current) {
      window.clearTimeout(stageTimer.current);
      stageTimer.current = null;
    }
    if (shouldRetry) {
      const next = retries + 1;
      setStage(null);
      setRetryHint(
        `OCR 模型首次加载中，${RETRY_DELAY_MS / 1000} 秒后自动重试（第 ${next}/${MAX_AUTO_RETRY} 次）…`,
      );
      retryTimer.current = window.setTimeout(() => void run(next), RETRY_DELAY_MS);
      return; // 保持 busy，重试期间按钮持续转圈
    }
    setBusy(false);
    setStage(null);
  };

  /** 已有原文后切换模式 / 修正原文：用原文重新提炼，不重复上传与识别 */
  const runOnSource = async (m: "keypoints" | "exam", text?: string) => {
    const src = text ?? result?.source_text ?? "";
    if (!src) return;
    setBusy(true);
    setStage("refine");
    setError("");
    setSaved(false);
    try {
      const r = await apiNotes.refine({ text: src, mode: m });
      applyResult(r);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
      setStage(null);
    }
  };

  const switchMode = (m: "keypoints" | "exam") => {
    setMode(m);
    if (result?.source_text) {
      void runOnSource(m);
    }
  };

  const startEditText = () => {
    setDraftText(result?.source_text ?? "");
    setEditingText(true);
    setShowText(false);
  };

  const cancelEditText = () => {
    setEditingText(false);
    setDraftText("");
  };

  /** 用修正后的原文重新提炼 */
  const applyEditedText = async () => {
    const trimmed = draftText.trim();
    if (!trimmed) {
      setError("修正后的原文不能为空");
      return;
    }
    setResult((prev) => (prev ? { ...prev, source_text: trimmed } : prev));
    setEditingText(false);
    setDraftText("");
    await runOnSource(mode, trimmed);
  };

  const save = async () => {
    if (!result) return;
    setSaving(true);
    try {
      await apiNotes.create({
        material_id: null,
        title: title.trim() || result.title || "未命名笔记",
        mode: result.mode,
        summary: result.summary,
        points: result.points,
        engine: result.engine,
      });
      setSaved(true);
      onSaved?.();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  /** 粘贴图片：Ctrl+V 直接上传截图（文档无法从剪贴板读取，走点击/拖拽） */
  const handlePaste = (e: React.ClipboardEvent) => {
    const items = e.clipboardData?.items;
    if (!items) return;
    for (let i = 0; i < items.length; i++) {
      const item = items[i];
      if (item.type.startsWith("image/")) {
        const f = item.getAsFile();
        if (f) {
          e.preventDefault();
          pick(f);
          break;
        }
      }
    }
  };

  const busyLabel =
    stage === "refine"
      ? "AI 提炼中…"
      : isImage
        ? "OCR 识别中…"
        : "文件解析中…";
  const Icon = result ? ScanText : file ? fileIcon(iconGroupOf(file.name)) : Upload;

  return (
    <div className="space-y-3" onPaste={handlePaste}>
      {/* 模式选择 + 提炼按钮 */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex overflow-hidden rounded-xl border border-slate-200 bg-white text-sm">
          {(Object.keys(NOTE_MODES) as ("keypoints" | "exam")[]).map((m) => (
            <button
              key={m}
              onClick={() => switchMode(m)}
              className={`px-3.5 py-2 transition ${
                mode === m
                  ? "bg-brand-600 font-medium text-white"
                  : "text-slate-500 hover:bg-brand-50"
              }`}
            >
              {NOTE_MODES[m]}
            </button>
          ))}
        </div>
        <button
          onClick={() => run()}
          disabled={busy || !file}
          className="flex items-center gap-1.5 rounded-xl bg-violet-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-violet-700 disabled:opacity-50"
        >
          {busy ? <Spinner /> : <Wand2 size={15} />}
          {busy ? busyLabel : file && result ? "重新提炼" : "开始提炼"}
        </button>
        <span className="ml-auto text-[11px] text-slate-400">
          {file ? `已选文件（${humanSize(file.size)}）` : "未选择文件"}
        </span>
      </div>

      {/* 文件选择：点击 / 拖拽 / 粘贴截图 */}
      {!file ? (
        <div
          onDragOver={(e) => {
            e.preventDefault();
            setDragOver(true);
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragOver(false);
            pick(e.dataTransfer.files[0]);
          }}
          onClick={() => inputRef.current?.click()}
          className={`cursor-pointer rounded-xl border border-dashed px-4 py-6 text-center transition ${
            dragOver
              ? "border-violet-400 bg-violet-50"
              : "border-slate-200 bg-slate-50/50 hover:border-violet-300 hover:bg-violet-50/40"
          }`}
        >
          <div className="flex flex-col items-center justify-center">
            <Upload size={20} className="text-slate-300" />
            <p className="mt-2 text-xs text-slate-500">
              点击选择 / 拖入文件 / Ctrl+V 粘贴截图：板书照片、课件截图、PDF 讲义、Word 笔记、Excel 表格
            </p>
            <p className="mt-1 text-[11px] text-slate-400">{refineSummaryText(catalog.groups)}</p>
          </div>
          <div className="mt-2 flex justify-center" onClick={(e) => e.stopPropagation()}>
            <FormatCatalog
              groups={groups}
              unknownNote={catalog.unknown_note}
              ocrAvailable={catalog.ocr_available}
              degraded={catalogDegraded}
            />
          </div>
          <input
            ref={inputRef}
            type="file"
            accept={accept}
            className="hidden"
            onChange={(e) => pick(e.target.files?.[0])}
          />
        </div>
      ) : (
        <div className="flex items-center gap-3 rounded-xl border border-slate-200 bg-slate-50/60 p-2.5">
          {preview ? (
            <img
              src={preview}
              alt="待识别图片预览"
              className="h-16 w-16 shrink-0 rounded-lg border border-slate-200 object-cover"
            />
          ) : (
            <span className="flex h-16 w-16 shrink-0 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-400">
              <Icon size={22} />
            </span>
          )}
          <div className="min-w-0 flex-1">
            <p className="truncate text-xs font-medium text-slate-700">{file.name}</p>
            <p className="mt-0.5 text-[11px] text-slate-400">
              {humanSize(file.size)}
              {result?.format_label ? ` · ${result.format_label}` : ""}
              {result ? " · 已抽取正文，可重新提炼" : " · 点击「开始提炼」抽正文并提炼"}
            </p>
          </div>
          <button
            onClick={clearFile}
            className="shrink-0 rounded-lg p-1.5 text-slate-400 hover:bg-red-50 hover:text-red-500"
            title="移除文件"
          >
            <Trash2 size={14} />
          </button>
        </div>
      )}

      {error && (
        <div className="rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-600">
          {error}
        </div>
      )}

      {retryHint && (
        <div className="flex items-center gap-2 rounded-xl border border-blue-200 bg-blue-50 px-3 py-2 text-xs text-blue-700">
          <Spinner />
          {retryHint}
        </div>
      )}

      {/* 结果预览 */}
      {result && (
        <div className="rounded-xl border border-slate-200 bg-slate-50/60 p-4">
          {/* 抽取提示：扫描件 OCR / 扩展名不一致 / 无文本层… */}
          {result.warnings.length > 0 && (
            <div className="mb-3 space-y-1.5">
              {result.warnings.map((w, i) => (
                <div
                  key={i}
                  className="flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700"
                >
                  <AlertTriangle size={14} className="mt-0.5 shrink-0" />
                  <span>{w}</span>
                </div>
              ))}
            </div>
          )}

          <div className="mb-2 flex items-center gap-2">
            <ScanText size={14} className="text-violet-500" />
            <input
              className="min-w-0 flex-1 rounded-lg border border-transparent bg-transparent px-2 py-1 text-sm font-semibold text-slate-800 outline-none transition hover:border-slate-200 focus:border-brand-400 focus:bg-white"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              title="笔记标题（可修改）"
            />
            <span
              className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] ${
                result.engine === "llm"
                  ? "bg-violet-100 text-violet-600"
                  : "bg-slate-200 text-slate-500"
              }`}
            >
              {result.engine === "llm" ? "AI 模型提炼" : "离线规则提炼"}
            </span>
          </div>
          {result.summary && (
            <p className="mb-2 border-l-2 border-violet-300 pl-2 text-xs leading-5 text-slate-600">
              {result.summary}
            </p>
          )}
          <ul className="space-y-1.5">
            {result.points.map((p, i) => (
              <li key={i} className="flex items-start gap-2 text-sm text-slate-700">
                <span className="mt-0.5 shrink-0 font-medium text-slate-300">{i + 1}.</span>
                <span className="min-w-0 flex-1 leading-5">{p.point}</span>
                <span
                  className={`mt-0.5 shrink-0 rounded-md px-1.5 py-0.5 text-[10px] font-medium ${IMPORTANCE_COLORS[p.importance] ?? IMPORTANCE_COLORS.medium}`}
                >
                  {IMPORTANCE_LABELS[p.importance] ?? IMPORTANCE_LABELS.medium}
                </span>
              </li>
            ))}
          </ul>
          {result.points.length === 0 && (
            <p className="text-xs text-slate-400">抽取出的原文太短，暂未提炼出要点</p>
          )}

          {/* 抽取原文：可核对 / 可编辑修正后重新提炼 */}
          <div className="mt-3">
            {!editingText ? (
              <div className="flex flex-wrap items-center gap-3">
                <button
                  onClick={() => setShowText((v) => !v)}
                  className="flex items-center gap-1 text-[11px] text-slate-400 hover:text-violet-600"
                >
                  <X size={11} className={`transition ${showText ? "rotate-45" : ""}`} />
                  {showText
                    ? "收起抽取原文"
                    : `核对抽取原文（${result.source_kind === "image" ? "OCR " : ""}${result.source_text.length} 字）`}
                </button>
                {result.source_text && (
                  <button
                    onClick={startEditText}
                    disabled={busy}
                    className="flex items-center gap-1 text-[11px] text-slate-400 hover:text-brand-600 disabled:opacity-50"
                    title="修正识别/抽取错误后重新提炼"
                  >
                    <Edit3 size={11} />
                    修正原文
                  </button>
                )}
                {result.truncated && (
                  <span className="text-[11px] text-amber-600">
                    原文共 {result.text_chars} 字，仅回传前 {result.source_text.length} 字供核对
                  </span>
                )}
              </div>
            ) : (
              <div className="flex flex-wrap items-center gap-3">
                <span className="flex items-center gap-1 text-[11px] font-medium text-brand-600">
                  <Edit3 size={11} />
                  正在修正抽取原文…
                </span>
                <button
                  onClick={applyEditedText}
                  disabled={busy}
                  className="flex items-center gap-1 text-[11px] font-medium text-brand-600 hover:underline disabled:opacity-50"
                >
                  {busy ? <Spinner /> : <RefreshCw size={11} />}
                  用修正后原文重新提炼
                </button>
                <button
                  onClick={cancelEditText}
                  disabled={busy}
                  className="text-[11px] text-slate-400 hover:text-slate-600 disabled:opacity-50"
                >
                  取消
                </button>
              </div>
            )}
            {showText && !editingText && (
              <pre className="mt-2 max-h-40 overflow-y-auto whitespace-pre-wrap rounded-lg border border-slate-200 bg-white p-2.5 text-[11px] leading-5 text-slate-500">
                {result.source_text || "（无）"}
              </pre>
            )}
            {editingText && (
              <textarea
                className="mt-2 h-40 w-full resize-none rounded-lg border border-brand-300 bg-white p-2.5 text-[11px] leading-5 text-slate-700 outline-none focus:border-brand-500"
                value={draftText}
                onChange={(e) => setDraftText(e.target.value)}
                placeholder="修正识别/抽取错误（如错别字、漏字、表格串行），完成后点击「用修正后原文重新提炼」"
                autoFocus
              />
            )}
          </div>

          <div className="mt-3 flex items-center gap-2">
            <button
              onClick={save}
              disabled={saving || saved || result.points.length === 0}
              className={`flex items-center gap-1.5 rounded-xl px-4 py-2 text-sm font-medium transition disabled:opacity-50 ${
                saved
                  ? "bg-emerald-50 text-emerald-600"
                  : "bg-brand-600 text-white hover:bg-brand-700"
              }`}
            >
              {saving ? <Spinner /> : saved ? <Check size={15} /> : <Save size={15} />}
              {saved ? "已保存到笔记" : "保存为笔记"}
            </button>
            <span className="text-[11px] text-slate-400">
              {NOTE_MODES[result.mode] ?? result.mode} · {result.points.length} 条要点
              {result.format_label ? ` · 来源 ${result.format_label}` : ""}
            </span>
          </div>
        </div>
      )}
    </div>
  );
}
