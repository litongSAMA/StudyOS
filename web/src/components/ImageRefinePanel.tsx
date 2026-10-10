import { useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  Check,
  Edit3,
  ImagePlus,
  RefreshCw,
  Save,
  ScanText,
  Trash2,
  Wand2,
  X,
} from "lucide-react";
import { apiNotes, ApiHttpError } from "../api";
import { IMPORTANCE_COLORS, IMPORTANCE_LABELS, NOTE_MODES } from "../types";
import type { ImageRefineResult, RefineResult } from "../types";
import { Spinner } from "./ui";

const MAX_BYTES = 20 * 1024 * 1024;

/**
 * 识图提炼面板：「AI 笔记提炼」页专用。
 *
 * 功能：
 * - 上传图片（点击 / 拖拽 / Ctrl+V 粘贴截图）→ OCR 识别文字 → 提炼重点/考点笔记
 * - 识别原文可展开核对，也可进入编辑模式修正 OCR 错误后重新提炼
 * - 切换模式时用已识别（或已修正）的原文直接重新提炼，无需再次上传
 * - OCR 质量警告（文字过少/噪声过多）即时提示
 */
export default function ImageRefinePanel({ onSaved }: { onSaved?: () => void }) {
  const [mode, setMode] = useState<"keypoints" | "exam">("keypoints");
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState("");
  const [dragOver, setDragOver] = useState(false);
  const [busy, setBusy] = useState(false);
  /** 当前处理阶段：ocr=OCR 识别中 / refine=AI 提炼中（前端估算，用于按钮文案） */
  const [stage, setStage] = useState<"ocr" | "refine" | null>(null);
  const [result, setResult] = useState<ImageRefineResult | null>(null);
  const [sourceText, setSourceText] = useState("");
  const [showText, setShowText] = useState(false);
  const [title, setTitle] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState("");
  // OCR 原文编辑状态
  const [editingText, setEditingText] = useState(false);
  const [draftText, setDraftText] = useState("");
  /** 503 模型加载中时的自动重试提示（空串=未在重试） */
  const [retryHint, setRetryHint] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const stageTimer = useRef<number | null>(null);
  const retryTimer = useRef<number | null>(null);

  // 卸载时回收预览对象 URL 与计时器
  useEffect(() => {
    return () => {
      if (preview) URL.revokeObjectURL(preview);
      if (stageTimer.current) window.clearTimeout(stageTimer.current);
      if (retryTimer.current) window.clearTimeout(retryTimer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const pick = (f: File | undefined | null) => {
    setError("");
    setSaved(false);
    setEditingText(false);
    if (!f) return;
    if (!f.type.startsWith("image/")) {
      setError("请选择图片文件（png / jpg / jpeg / webp / bmp）");
      return;
    }
    if (f.size > MAX_BYTES) {
      setError("图片超过 20MB，请压缩后重试");
      return;
    }
    if (preview) URL.revokeObjectURL(preview);
    setPreview(URL.createObjectURL(f));
    setFile(f);
    setResult(null);
    setSourceText("");
    setShowText(false);
  };

  const clearFile = () => {
    if (preview) URL.revokeObjectURL(preview);
    if (retryTimer.current) window.clearTimeout(retryTimer.current);
    setPreview("");
    setFile(null);
    setResult(null);
    setSourceText("");
    setShowText(false);
    setError("");
    setRetryHint("");
    setEditingText(false);
    setDraftText("");
    if (inputRef.current) inputRef.current.value = "";
  };

  const applyResult = (r: RefineResult, srcText?: string) => {
    const ir: ImageRefineResult = {
      ...r,
      source_text: srcText ?? sourceText,
      ocr_available: true,
      // 文本重新提炼（切换模式/修正原文）不产生新的 OCR 警告，保留上一次的
      ocr_warning: (r as ImageRefineResult).ocr_warning ?? result?.ocr_warning ?? "",
    };
    setResult(ir);
    setTitle(r.title);
    setSaved(false);
  };

  /** 首次：上传图片 → OCR → 提炼。503（模型首次加载中）时自动延迟重试。 */
  const MAX_AUTO_RETRY = 3;
  const RETRY_DELAY_MS = 3000;
  const run = async (retries = 0) => {
    if (!file) return;
    setBusy(true);
    setStage("ocr");
    setError("");
    setRetryHint("");
    setSaved(false);
    setEditingText(false);
    // 后端单次请求返回，前端按时间估算阶段：前 2 秒大概率在 OCR，之后在 AI 提炼
    if (stageTimer.current) window.clearTimeout(stageTimer.current);
    stageTimer.current = window.setTimeout(() => setStage("refine"), 2000);
    let shouldRetry = false;
    try {
      const r = await apiNotes.refineImage(file, mode);
      setSourceText(r.source_text);
      applyResult(r, r.source_text);
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

  /** 已识别原文后切换模式 / 修正原文：用原文重新提炼，不重复上传/OCR */
  const runOnSource = async (m: "keypoints" | "exam", text?: string) => {
    const src = text ?? sourceText;
    if (!src) return;
    setBusy(true);
    setStage("refine");
    setError("");
    setSaved(false);
    try {
      const r = await apiNotes.refine({ text: src, mode: m });
      applyResult(r, src);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
      setStage(null);
    }
  };

  const switchMode = (m: "keypoints" | "exam") => {
    setMode(m);
    if (sourceText) {
      void runOnSource(m);
    }
  };

  /** 进入 OCR 原文编辑模式 */
  const startEditText = () => {
    setDraftText(sourceText);
    setEditingText(true);
    setShowText(false);
  };

  /** 取消编辑，还原 */
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
    setSourceText(trimmed);
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

  /** 粘贴图片：Ctrl+V 直接上传截图 */
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

  const busyLabel = stage === "ocr" ? "OCR 识别中…" : "AI 提炼中…";

  return (
    <div className="space-y-3" onPaste={handlePaste}>
      {/* 模式选择 + 识图提炼按钮 */}
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
          {busy ? busyLabel : "识图提炼"}
        </button>
        <span className="ml-auto text-[11px] text-slate-400">
          {file ? `已选图片（${(file.size / 1024).toFixed(0)} KB）` : "未选择图片"}
        </span>
      </div>

      {/* 图片选择：点击 / 拖拽 / 粘贴 */}
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
          className={`flex cursor-pointer flex-col items-center justify-center rounded-xl border border-dashed px-4 py-6 text-center transition ${
            dragOver
              ? "border-violet-400 bg-violet-50"
              : "border-slate-200 bg-slate-50/50 hover:border-violet-300 hover:bg-violet-50/40"
          }`}
        >
          <ImagePlus size={20} className="text-slate-300" />
          <p className="mt-2 text-xs text-slate-500">
            点击选择 / 拖入图片 / Ctrl+V 粘贴截图：课堂板书 / 课件截图 / 课本照片
          </p>
          <p className="mt-1 text-[11px] text-slate-400">
            支持 png / jpg / jpeg / webp / bmp，最大 20MB，OCR 离线识别
          </p>
          <input
            ref={inputRef}
            type="file"
            accept="image/png,image/jpeg,image/webp,image/bmp"
            className="hidden"
            onChange={(e) => pick(e.target.files?.[0])}
          />
        </div>
      ) : (
        <div className="flex items-center gap-3 rounded-xl border border-slate-200 bg-slate-50/60 p-2.5">
          <img
            src={preview}
            alt="待识别图片预览"
            className="h-16 w-16 shrink-0 rounded-lg border border-slate-200 object-cover"
          />
          <div className="min-w-0 flex-1">
            <p className="truncate text-xs font-medium text-slate-700">{file.name}</p>
            <p className="mt-0.5 text-[11px] text-slate-400">
              {(file.size / 1024).toFixed(0)} KB · 点击「识图提炼」开始识别
            </p>
          </div>
          <button
            onClick={clearFile}
            className="shrink-0 rounded-lg p-1.5 text-slate-400 hover:bg-red-50 hover:text-red-500"
            title="移除图片"
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
          {/* OCR 质量警告 */}
          {result.ocr_warning && (
            <div className="mb-3 flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700">
              <AlertTriangle size={14} className="mt-0.5 shrink-0" />
              <span>{result.ocr_warning}</span>
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
              {result.engine === "llm" ? "识图 + AI 模型提炼" : "识图 + 离线规则提炼"}
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
            <p className="text-xs text-slate-400">识别出的原文太短，暂未提炼出要点</p>
          )}

          {/* 识别原文：可核对 / 可编辑修正后重新提炼 */}
          <div className="mt-3">
            {!editingText ? (
              <div className="flex flex-wrap items-center gap-3">
                <button
                  onClick={() => setShowText((v) => !v)}
                  className="flex items-center gap-1 text-[11px] text-slate-400 hover:text-violet-600"
                >
                  <X size={11} className={`transition ${showText ? "rotate-45" : ""}`} />
                  {showText ? "收起识别原文" : `核对识别原文（${sourceText.length} 字）`}
                </button>
                {sourceText && (
                  <button
                    onClick={startEditText}
                    disabled={busy}
                    className="flex items-center gap-1 text-[11px] text-slate-400 hover:text-brand-600 disabled:opacity-50"
                    title="修正 OCR 识别错误后重新提炼"
                  >
                    <Edit3 size={11} />
                    修正原文
                  </button>
                )}
              </div>
            ) : (
              <div className="flex flex-wrap items-center gap-3">
                <span className="flex items-center gap-1 text-[11px] font-medium text-brand-600">
                  <Edit3 size={11} />
                  正在修正识别原文…
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
                {sourceText || "（无）"}
              </pre>
            )}
            {editingText && (
              <textarea
                className="mt-2 h-40 w-full resize-none rounded-lg border border-brand-300 bg-white p-2.5 text-[11px] leading-5 text-slate-700 outline-none focus:border-brand-500"
                value={draftText}
                onChange={(e) => setDraftText(e.target.value)}
                placeholder="修正 OCR 识别错误（如错别字、漏字、多余符号），完成后点击「用修正后原文重新提炼」"
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
            </span>
          </div>
        </div>
      )}
    </div>
  );
}
