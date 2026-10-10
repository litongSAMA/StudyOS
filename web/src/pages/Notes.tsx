import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  BrainCircuit,
  BookOpen,
  CircleHelp,
  Download,
  Pencil,
  Plus,
  ScanText,
  Sparkles,
  Trash2,
  Wand2,
} from "lucide-react";
import { apiNotes } from "../api";
import { useApp } from "../App";
import DownloadLink from "../components/DownloadLink";
import FileRefinePanel from "../components/FileRefinePanel";
import RefinePanel from "../components/RefinePanel";
import { Empty, Modal, Spinner } from "../components/ui";
import {
  IMPORTANCE_COLORS,
  IMPORTANCE_LABELS,
  NOTE_MODES,
} from "../types";
import type { Note, RefinePoint } from "../types";
import { fmtDT } from "../utils";

export default function Notes() {
  const { refreshKey } = useApp();
  const [notes, setNotes] = useState<Note[]>([]);
  const [freeText, setFreeText] = useState("");
  const [loading, setLoading] = useState(false);
  const [editing, setEditing] = useState<Note | null>(null);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  const load = async () => {
    setLoading(true);
    try {
      setNotes(await apiNotes.list());
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    load();
  }, [refreshKey]);

  const remove = async (n: Note) => {
    if (!confirm(`删除笔记「${n.title}」？`)) return;
    await apiNotes.remove(n.id);
    load();
  };

  const toggle = (id: number) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-xl font-bold text-slate-800">AI 笔记提炼</h1>
        <p className="mt-1 text-sm text-slate-500">
          从资料全文、任意文本，或上传图片 / PDF / Word / Excel / PPT 等文件，一键提炼「重点笔记
          / 考点笔记」，可编辑、可导出 Markdown
        </p>
      </div>

      <div className="grid gap-4 lg:grid-cols-5">
        {/* 自由提炼 + 识图提炼 */}
        <div className="space-y-3 lg:col-span-2">
          <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
            <div className="mb-2 flex items-center gap-2 text-sm font-semibold text-slate-700">
              <Sparkles size={16} className="text-violet-500" />
              自由提炼
            </div>
            <textarea
              rows={6}
              className="w-full resize-none rounded-xl border border-slate-200 px-3.5 py-3 text-sm outline-none transition focus:border-brand-500"
              placeholder={"粘贴课堂笔记 / 讲义 / 通知原文，例如：\n\n死锁的四个必要条件：互斥、请求与保持、不可剥夺、循环等待。考试重点：银行家算法安全性检测步骤…"}
              value={freeText}
              onChange={(e) => setFreeText(e.target.value)}
            />
            <div className="mt-3">
              <RefinePanel
                text={freeText}
                onSaved={() => {
                  setFreeText("");
                  load();
                }}
              />
            </div>
          </div>

          <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
            <div className="mb-2 flex items-center gap-2 text-sm font-semibold text-slate-700">
              <ScanText size={16} className="text-violet-500" />
              识图 / 文档提炼
            </div>
            <FileRefinePanel onSaved={() => load()} />
          </div>
        </div>

        {/* 已保存笔记 */}
        <div className="lg:col-span-3">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-sm font-semibold text-slate-700">
              已保存笔记（{notes.length}）
            </h2>
            <DownloadLink
              path={apiNotes.mdUrl()}
              fallbackName="studyos-notes.md"
              className="flex items-center gap-1 text-xs text-brand-600 hover:underline"
            >
              <Download size={13} /> 全部导出 Markdown
            </DownloadLink>
          </div>

          {loading && (
            <div className="flex items-center gap-2 text-sm text-slate-400">
              <Spinner /> 加载中…
            </div>
          )}

          {notes.length === 0 && !loading ? (
            <Empty text="还没有提炼笔记——左边粘贴文本，或上传图片 / PDF / Word / Excel 等文件提炼；也可去「资料库」对资料点「提炼笔记」" />
          ) : (
            <div className="space-y-3">
              {notes.map((n) => {
                const isOpen = expanded.has(n.id);
                return (
                  <div
                    key={n.id}
                    className="group rounded-2xl border border-slate-200 bg-white p-4 shadow-sm transition hover:border-brand-200"
                  >
                    <div className="flex items-start gap-3">
                      <div className="mt-0.5 rounded-lg bg-violet-100 p-2 text-violet-600">
                        <BrainCircuit size={15} />
                      </div>
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <h3 className="text-sm font-semibold text-slate-800">{n.title}</h3>
                          <span
                            className={`rounded-md px-1.5 py-0.5 text-[10px] font-medium ${
                              n.mode === "exam"
                                ? "bg-red-100 text-red-600"
                                : "bg-violet-100 text-violet-600"
                            }`}
                          >
                            {NOTE_MODES[n.mode] ?? n.mode}
                          </span>
                          <span className="rounded-md bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-500">
                            {n.points.length} 条要点
                          </span>
                        </div>
                        <div className="mt-0.5 flex items-center gap-1 text-[11px] text-slate-400">
                          {n.material_title && (
                            <span className="inline-flex items-center gap-0.5">
                              <BookOpen size={10} />
                              {n.material_title}
                              {n.course_name && `（${n.course_name}）`}
                              <span className="mx-1">·</span>
                            </span>
                          )}
                          {fmtDT(n.created_at)}
                          {n.engine === "llm" && (
                            <span className="ml-1 rounded bg-violet-50 px-1 text-violet-500">
                              AI
                            </span>
                          )}
                        </div>
                        {n.summary && (
                          <p className="mt-1.5 line-clamp-2 text-xs leading-5 text-slate-500">
                            {n.summary}
                          </p>
                        )}
                        {isOpen && (
                          <ul className="mt-2 space-y-1.5 border-t border-slate-100 pt-2">
                            {n.points.map((p, i) => (
                              <li key={i} className="flex items-start gap-2 text-sm text-slate-700">
                                <span className="mt-0.5 shrink-0 text-xs font-medium text-slate-300">
                                  {i + 1}.
                                </span>
                                <span className="min-w-0 flex-1 leading-5">{p.point}</span>
                                <span
                                  className={`mt-0.5 shrink-0 rounded-md px-1.5 py-0.5 text-[10px] font-medium ${IMPORTANCE_COLORS[p.importance] ?? IMPORTANCE_COLORS.medium}`}
                                >
                                  {IMPORTANCE_LABELS[p.importance] ?? IMPORTANCE_LABELS.medium}
                                </span>
                              </li>
                            ))}
                          </ul>
                        )}
                      </div>
                      <div className="flex shrink-0 gap-1 opacity-0 transition group-hover:opacity-100">
                        <Link
                          to={`/quiz?notes=${n.id}`}
                          className="rounded-lg p-1.5 text-slate-400 hover:bg-violet-50 hover:text-violet-600"
                          title="基于这条笔记出题抽背"
                        >
                          <CircleHelp size={14} />
                        </Link>
                        <button
                          className="rounded-lg p-1.5 text-slate-400 hover:bg-violet-50 hover:text-violet-600"
                          title="展开 / 收起要点"
                          onClick={() => toggle(n.id)}
                        >
                          <Plus size={14} className={`transition ${isOpen ? "rotate-45" : ""}`} />
                        </button>
                        <DownloadLink
                          className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100"
                          title="导出 Markdown"
                          path={apiNotes.mdUrl(n.id)}
                          fallbackName={`studyos-note-${n.id}.md`}
                        >
                          <Download size={14} />
                        </DownloadLink>
                        <button
                          className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100"
                          title="编辑"
                          onClick={() => setEditing(n)}
                        >
                          <Pencil size={14} />
                        </button>
                        <button
                          className="rounded-lg p-1.5 text-slate-400 hover:bg-red-50 hover:text-red-500"
                          title="删除"
                          onClick={() => remove(n)}
                        >
                          <Trash2 size={14} />
                        </button>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>

      {editing && (
        <Modal title="编辑笔记" onClose={() => setEditing(null)} wide>
          <NoteEditForm
            note={editing}
            onDone={() => {
              setEditing(null);
              load();
            }}
          />
        </Modal>
      )}
    </div>
  );
}

function NoteEditForm({ note, onDone }: { note: Note; onDone: () => void }) {
  const [title, setTitle] = useState(note.title);
  const [mode, setMode] = useState(note.mode);
  const [summary, setSummary] = useState(note.summary);
  const [points, setPoints] = useState<RefinePoint[]>(note.points.map((p) => ({ ...p })));
  const [saving, setSaving] = useState(false);

  const setPoint = (i: number, patch: Partial<RefinePoint>) => {
    setPoints((prev) => prev.map((p, j) => (j === i ? { ...p, ...patch } : p)));
  };

  const save = async () => {
    setSaving(true);
    try {
      await apiNotes.patch(note.id, {
        title: title.trim() || note.title,
        mode,
        summary,
        points: points.filter((p) => p.point.trim()),
      });
      onDone();
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-3 gap-3">
        <label className="col-span-2 block text-xs text-slate-500">
          标题
          <input
            className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2 text-sm"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
        </label>
        <label className="block text-xs text-slate-500">
          模式
          <select
            className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2 text-sm"
            value={mode}
            onChange={(e) => setMode(e.target.value)}
          >
            {Object.entries(NOTE_MODES).map(([k, v]) => (
              <option key={k} value={k}>{v}</option>
            ))}
          </select>
        </label>
      </div>
      <label className="block text-xs text-slate-500">
        概要
        <textarea
          rows={2}
          className="mt-1 w-full resize-none rounded-xl border border-slate-200 px-3 py-2 text-sm"
          value={summary}
          onChange={(e) => setSummary(e.target.value)}
        />
      </label>
      <div className="text-xs text-slate-500">
        要点
        <div className="mt-1 max-h-64 space-y-2 overflow-y-auto rounded-xl border border-slate-200 p-3">
          {points.map((p, i) => (
            <div key={i} className="flex items-start gap-2">
              <span className="mt-2 shrink-0 text-xs font-medium text-slate-300">{i + 1}.</span>
              <textarea
                rows={1}
                className="min-w-0 flex-1 resize-none rounded-lg border border-slate-200 px-2.5 py-1.5 text-sm"
                value={p.point}
                onChange={(e) => setPoint(i, { point: e.target.value })}
              />
              <select
                className="mt-1 shrink-0 rounded-lg border border-slate-200 px-2 py-1.5 text-xs"
                value={p.importance}
                onChange={(e) => setPoint(i, { importance: e.target.value })}
                title="重要性"
              >
                {Object.entries(IMPORTANCE_LABELS).map(([k, v]) => (
                  <option key={k} value={k}>{v}</option>
                ))}
              </select>
              <button
                className="mt-1.5 shrink-0 rounded-lg p-1 text-slate-300 hover:bg-red-50 hover:text-red-500"
                title="删除此要点"
                onClick={() => setPoints((prev) => prev.filter((_, j) => j !== i))}
              >
                <Trash2 size={13} />
              </button>
            </div>
          ))}
          <button
            className="flex w-full items-center justify-center gap-1 rounded-lg border border-dashed border-slate-200 py-2 text-xs text-slate-400 hover:border-brand-300 hover:text-brand-600"
            onClick={() =>
              setPoints((prev) => [...prev, { point: "", importance: "medium" }])
            }
          >
            <Wand2 size={12} /> 添加要点
          </button>
        </div>
      </div>
      <button
        onClick={save}
        disabled={saving}
        className="w-full rounded-xl bg-brand-600 py-2.5 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-50"
      >
        保存
      </button>
    </div>
  );
}
