import { useEffect, useRef, useState } from "react";
import { BrainCircuit, Check, Save, Sparkles, Wand2 } from "lucide-react";
import { apiNotes } from "../api";
import { IMPORTANCE_COLORS, IMPORTANCE_LABELS, NOTE_MODES } from "../types";
import type { RefineResult } from "../types";
import { Spinner } from "./ui";

/**
 * 提炼面板（共享组件）：模式选择 → AI 提炼 → 预览 → 保存为笔记。
 * 资料库卡片弹窗与「笔记」页自由提炼共用同一套交互。
 */
export default function RefinePanel({
  text,
  materialId,
  materialTitle,
  onSaved,
  autoRun,
}: {
  /** 提炼的原文（不传则用 materialId 对应资料的全文） */
  text?: string;
  materialId?: number;
  materialTitle?: string;
  onSaved?: () => void;
  /** 打开时自动提炼一次（默认 false） */
  autoRun?: boolean;
}) {
  const [mode, setMode] = useState<"keypoints" | "exam">("keypoints");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<RefineResult | null>(null);
  const [title, setTitle] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState("");
  const ranRef = useRef(false);

  const run = async () => {
    setBusy(true);
    setError("");
    setSaved(false);
    try {
      const r = await apiNotes.refine({
        material_id: materialId,
        text: text?.trim() ? text : undefined,
        mode,
      });
      setResult(r);
      setTitle(r.title);
      ranRef.current = true;
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  // 打开即提炼（一次性）
  useEffect(() => {
    if (autoRun && !ranRef.current) {
      run();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const save = async () => {
    if (!result) return;
    setSaving(true);
    try {
      await apiNotes.create({
        material_id: materialId ?? null,
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

  const srcLabel = materialTitle
    ? `来源资料：${materialTitle}`
    : text?.trim()
      ? `粘贴文本（${text.trim().length} 字）`
      : "未提供原文";

  return (
    <div className="space-y-3">
      {/* 模式选择 + 提炼按钮 */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex overflow-hidden rounded-xl border border-slate-200 bg-white text-sm">
          {(Object.keys(NOTE_MODES) as ("keypoints" | "exam")[]).map((m) => (
            <button
              key={m}
              onClick={() => {
                setMode(m);
                setSaved(false);
              }}
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
          onClick={run}
          disabled={busy || (!materialId && !text?.trim())}
          className="flex items-center gap-1.5 rounded-xl bg-violet-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-violet-700 disabled:opacity-50"
        >
          {busy ? <Spinner /> : <Wand2 size={15} />}
          开始提炼
        </button>
        <span className="ml-auto text-[11px] text-slate-400">{srcLabel}</span>
      </div>

      {error && (
        <div className="rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-600">
          {error}
        </div>
      )}

      {/* 结果预览 */}
      {result && (
        <div className="rounded-xl border border-slate-200 bg-slate-50/60 p-4">
          <div className="mb-2 flex items-center gap-2">
            <BrainCircuit size={14} className="text-violet-500" />
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
            <p className="text-xs text-slate-400">原文太短，暂未提炼出要点</p>
          )}
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

      {!result && !busy && !error && (
        <div className="rounded-xl border border-dashed border-slate-200 px-4 py-6 text-center">
          <Sparkles size={18} className="mx-auto text-slate-300" />
          <p className="mt-2 text-xs text-slate-400">
            选择「重点笔记」提炼核心概念与结论，选择「考点笔记」提炼必考/常考/易错点
          </p>
        </div>
      )}
    </div>
  );
}
