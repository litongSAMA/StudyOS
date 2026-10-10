import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import {
  BookX,
  Check,
  ChevronDown,
  Download,
  Eye,
  EyeOff,
  Pencil,
  Plus,
  RotateCcw,
  Search,
  Trash2,
  Wand2,
  X,
} from "lucide-react";
import { apiCourses, apiWrongbook } from "../api";
import { useApp } from "../App";
import DownloadLink from "../components/DownloadLink";
import { Empty, Modal, Spinner } from "../components/ui";
import {
  DIFF_COLORS,
  DIFFICULTIES,
  WRONG_STATUS,
} from "../types";
import type { Course, WrongBookFacets, WrongBookStats, WrongQuestion } from "../types";
import { fmtDT } from "../utils";

const STATUS_TABS = [
  { value: "", label: "全部" },
  { value: "open", label: "待攻克" },
  { value: "done", label: "已掌握" },
];

export default function Wrongbook() {
  const { refreshKey } = useApp();
  const [courses, setCourses] = useState<Course[]>([]);
  const [stats, setStats] = useState<WrongBookStats | null>(null);
  const [facets, setFacets] = useState<WrongBookFacets>({ chapters: [], tags: [] });
  const [items, setItems] = useState<WrongQuestion[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);

  // 过滤条件
  const [courseId, setCourseId] = useState<number | "">("");
  const [status, setStatus] = useState("");
  const [chapter, setChapter] = useState("");
  const [difficulty, setDifficulty] = useState("");
  const [q, setQ] = useState("");
  const [selTags, setSelTags] = useState<string[]>([]);
  const [tagMode, setTagMode] = useState<"any" | "all">("any");

  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [practicingId, setPracticingId] = useState<number | null>(null);
  const [editing, setEditing] = useState<WrongQuestion | null>(null);
  const [creating, setCreating] = useState(false);
  const [practiceOpen, setPracticeOpen] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const r = await apiWrongbook.list({
        course_id: courseId === "" ? undefined : courseId,
        status: status || undefined,
        chapter: chapter || undefined,
        difficulty: difficulty || undefined,
        q: q.trim() || undefined,
        tags: selTags.length ? selTags : undefined,
        tag_mode: tagMode,
        limit: 500,
      });
      setItems(r.items);
      setTotal(r.meta.total);
    } catch (e) {
      alert((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  const refreshAll = async () => {
    setStats(await apiWrongbook.stats());
    setFacets(await apiWrongbook.facets());
  };

  useEffect(() => {
    apiCourses.list().then(setCourses).catch(console.error);
    refreshAll().catch(console.error);
  }, [refreshKey]);

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [courseId, status, chapter, difficulty, q, selTags, tagMode, refreshKey]);

  const toggle = (id: number) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
    setPracticingId(null);
  };

  const remove = async (w: WrongQuestion) => {
    if (!confirm(`删除错题「${w.title}」？`)) return;
    await apiWrongbook.remove(w.id);
    load();
    refreshAll();
  };

  const toggleTag = (t: string) => {
    setSelTags((prev) => (prev.includes(t) ? prev.filter((x) => x !== t) : [...prev, t]));
  };

  const afterMutation = () => {
    load();
    refreshAll();
  };

  return (
    <div className="mobile-page space-y-5">
      <div className="mobile-page-heading flex items-start justify-between">
        <div>
          <h1 className="text-xl font-bold text-slate-800">错题本</h1>
          <p className="mt-1 text-sm text-slate-500">
            把试卷/作业里做错的题归集到一起——自动补章节与难度，随时重练，连续答对 2 次自动「已掌握」
          </p>
        </div>
        <div className="mobile-page-actions flex items-center gap-2">
          <DownloadLink
            path={apiWrongbook.mdUrl()}
            fallbackName="studyos-wrongbook.md"
            className="flex items-center gap-1.5 rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs font-medium text-slate-600 transition hover:border-brand-300 hover:text-brand-600"
          >
            <Download size={14} /> 导出 Markdown
          </DownloadLink>
          <button
            onClick={() => setPracticeOpen(true)}
            disabled={!stats?.open}
            className="flex items-center gap-1.5 rounded-xl border border-violet-200 bg-violet-50 px-3 py-2 text-xs font-medium text-violet-600 transition hover:bg-violet-100 disabled:cursor-not-allowed disabled:opacity-40"
          >
            <RotateCcw size={14} /> 重练模式{stats?.open ? `（${stats.open}）` : ""}
          </button>
          <button
            onClick={() => setCreating(true)}
            className="flex items-center gap-1.5 rounded-xl bg-brand-600 px-3.5 py-2 text-xs font-medium text-white transition hover:bg-brand-700"
          >
            <Plus size={14} /> 添加错题
          </button>
        </div>
      </div>

      {/* 统计卡片 */}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        {[
          { label: "全部错题", value: stats?.total ?? "–", cls: "text-slate-700" },
          { label: "待攻克", value: stats?.open ?? "–", cls: "text-red-600" },
          { label: "已掌握", value: stats?.done ?? "–", cls: "text-emerald-600" },
          { label: "掌握率", value: stats ? `${stats.mastered_pct}%` : "–", cls: "text-brand-600" },
        ].map(({ label, value, cls }) => (
          <div key={label} className="rounded-2xl border border-slate-200 bg-white px-4 py-3 shadow-sm">
            <div className={`text-xl font-bold ${cls}`}>{value}</div>
            <div className="text-xs text-slate-400">{label}</div>
          </div>
        ))}
      </div>

      {/* 过滤栏 */}
      <div className="space-y-3 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
        <div className="mobile-filter-bar flex flex-wrap items-center gap-2">
          <div className="mobile-filter-search relative min-w-48 flex-1">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-300" />
            <input
              className="w-full rounded-xl border border-slate-200 py-2 pl-8 pr-3 text-sm outline-none transition focus:border-brand-500"
              placeholder="搜题干 / 答案 / 错因 / 来源…"
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
          </div>
          <select
            className="rounded-xl border border-slate-200 px-3 py-2 text-sm outline-none"
            value={courseId}
            onChange={(e) => setCourseId(e.target.value === "" ? "" : Number(e.target.value))}
          >
            <option value="">全部课程</option>
            {courses.map((c) => (
              <option key={c.id} value={c.id}>{c.name}</option>
            ))}
          </select>
          <select
            className="rounded-xl border border-slate-200 px-3 py-2 text-sm outline-none"
            value={chapter}
            onChange={(e) => setChapter(e.target.value)}
          >
            <option value="">全部章节</option>
            {facets.chapters.map((c) => (
              <option key={c.chapter} value={c.chapter}>{c.chapter}（{c.count}）</option>
            ))}
          </select>
          <select
            className="rounded-xl border border-slate-200 px-3 py-2 text-sm outline-none"
            value={difficulty}
            onChange={(e) => setDifficulty(e.target.value)}
          >
            <option value="">全部难度</option>
            {Object.entries(DIFFICULTIES).filter(([k]) => k).map(([k, v]) => (
              <option key={k} value={k}>{v}</option>
            ))}
          </select>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {STATUS_TABS.map((t) => (
            <button
              key={t.value}
              onClick={() => setStatus(t.value)}
              className={`rounded-lg px-2.5 py-1 text-xs font-medium transition ${
                status === t.value
                  ? t.value === "done"
                    ? "bg-emerald-100 text-emerald-700"
                    : t.value === "open"
                      ? "bg-red-100 text-red-600"
                      : "bg-slate-800 text-white"
                  : "bg-slate-100 text-slate-500 hover:bg-slate-200"
              }`}
            >
              {t.label}
              {t.value === "open" && stats ? ` ${stats.open}` : ""}
              {t.value === "done" && stats ? ` ${stats.done}` : ""}
            </button>
          ))}

          {facets.tags.length > 0 && (
            <>
              <span className="mx-1 h-4 w-px bg-slate-200" />
              {selTags.length >= 2 && (
                <div className="flex overflow-hidden rounded-lg border border-slate-200 text-[11px]">
                  {(["any", "all"] as const).map((m) => (
                    <button
                      key={m}
                      onClick={() => setTagMode(m)}
                      className={`px-2 py-1 transition ${
                        tagMode === m ? "bg-brand-600 text-white" : "bg-white text-slate-400 hover:text-slate-600"
                      }`}
                      title={m === "all" ? "同时含全部所选标签" : "含任一所选标签"}
                    >
                      {m === "all" ? "全部匹配" : "任一匹配"}
                    </button>
                  ))}
                </div>
              )}
              {facets.tags.slice(0, 12).map((t) => {
                const on = selTags.includes(t.tag);
                return (
                  <button
                    key={t.tag}
                    onClick={() => toggleTag(t.tag)}
                    className={`rounded-lg px-2 py-1 text-xs transition ${
                      on
                        ? "bg-brand-600 text-white"
                        : "bg-slate-100 text-slate-500 hover:bg-slate-200"
                    }`}
                  >
                    {t.tag} {t.count}
                  </button>
                );
              })}
            </>
          )}
        </div>
      </div>

      {/* 列表 */}
      {loading ? (
        <div className="flex items-center gap-2 text-sm text-slate-400">
          <Spinner /> 加载中…
        </div>
      ) : items.length === 0 ? (
        <Empty text={total === 0 && !q && !selTags.length && !chapter && !difficulty && !courseId
          ? "错题本还是空的——点「添加错题」归集第一道题吧"
          : "没有匹配的错题，换个过滤条件试试"} />
      ) : (
        <div className="space-y-3">
          <div className="text-xs text-slate-400">共 {total} 题，待攻克排前</div>
          {items.map((w) => {
            const isOpen = expanded.has(w.id);
            const isPracticing = practicingId === w.id;
            return (
              <WrongCard
                key={w.id}
                w={w}
                isOpen={isOpen}
                isPracticing={isPracticing}
                onToggle={() => toggle(w.id)}
                onStartPractice={() => {
                  setExpanded((prev) => new Set(prev).add(w.id));
                  setPracticingId(w.id);
                }}
                onEndPractice={() => setPracticingId(null)}
                onGrade={async (ok) => {
                  await apiWrongbook.practice(w.id, ok);
                  afterMutation();
                }}
                onEdit={() => setEditing(w)}
                onDelete={() => remove(w)}
              />
            );
          })}
        </div>
      )}

      {/* 新建 / 编辑 */}
      {(creating || editing) && (
        <Modal title={editing ? "编辑错题" : "归集一道错题"} onClose={() => { setCreating(false); setEditing(null); }} wide>
          <WrongForm
            initial={editing}
            courses={courses}
            onDone={() => {
              setCreating(false);
              setEditing(null);
              afterMutation();
            }}
          />
        </Modal>
      )}

      {/* 重练模式 */}
      {practiceOpen && (
        <PracticeModal
          courseId={courseId === "" ? undefined : courseId}
          onClose={() => setPracticeOpen(false)}
          onFinished={afterMutation}
        />
      )}
    </div>
  );
}

/** 单条错题卡片：折叠显示题目/我的答案/正确答案/错因；可单题重练 */
function WrongCard({
  w,
  isOpen,
  isPracticing,
  onToggle,
  onStartPractice,
  onEndPractice,
  onGrade,
  onEdit,
  onDelete,
}: {
  w: WrongQuestion;
  isOpen: boolean;
  isPracticing: boolean;
  onToggle: () => void;
  onStartPractice: () => void;
  onEndPractice: () => void;
  onGrade: (ok: boolean) => Promise<void>;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const [revealed, setRevealed] = useState(false);
  const [grading, setGrading] = useState(false);
  const st = WRONG_STATUS[w.status] ?? WRONG_STATUS.open;

  const grade = async (ok: boolean) => {
    setGrading(true);
    try {
      await onGrade(ok);
      setRevealed(false);
      onEndPractice();
    } catch (e) {
      alert((e as Error).message);
    } finally {
      setGrading(false);
    }
  };

  const section = (label: string, cls: string, text: string) =>
    text ? (
      <div className="mt-2">
        <div className={`text-[11px] font-semibold ${cls}`}>{label}</div>
        <div className="mt-0.5 whitespace-pre-wrap text-sm leading-6 text-slate-700">{text}</div>
      </div>
    ) : null;

  return (
    <div className="group rounded-2xl border border-slate-200 bg-white p-4 shadow-sm transition hover:border-brand-200">
      <div className="flex items-start gap-3">
        <div className="mt-0.5 rounded-lg bg-rose-100 p-2 text-rose-600">
          <BookX size={15} />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-sm font-semibold text-slate-800">{w.title}</h3>
            <span className={`rounded-md px-1.5 py-0.5 text-[10px] font-medium ${st.cls}`}>
              {st.label}
            </span>
            {w.difficulty && (
              <span className={`rounded-md px-1.5 py-0.5 text-[10px] font-medium ${DIFF_COLORS[w.difficulty]}`}>
                {DIFFICULTIES[w.difficulty]}
              </span>
            )}
            {w.review_count > 0 && (
              <span className="rounded-md bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-500">
                重练 {w.review_count} 次{` · 错 ${w.wrong_count} 次`}
              </span>
            )}
          </div>
          <div className="mt-0.5 flex flex-wrap items-center gap-x-2 text-[11px] text-slate-400">
            {w.course_name && <span>{w.course_name}</span>}
            {w.source && <span>来源：{w.source}</span>}
            {w.chapter && <span>{w.chapter}</span>}
            <span>{fmtDT(w.created_at)}</span>
          </div>
          {w.tags.length > 0 && (
            <div className="mt-1.5 flex flex-wrap gap-1">
              {w.tags.map((t) => (
                <span key={t} className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-500">
                  {t}
                </span>
              ))}
            </div>
          )}
        </div>
        <div className="flex shrink-0 gap-1 opacity-0 transition group-hover:opacity-100">
          <button
            className="rounded-lg p-1.5 text-slate-400 hover:bg-rose-50 hover:text-rose-600"
            title="重练此题"
            onClick={onStartPractice}
          >
            <RotateCcw size={14} />
          </button>
          <button
            className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100"
            title="展开 / 收起"
            onClick={onToggle}
          >
            <ChevronDown size={14} className={`transition ${isOpen ? "rotate-180" : ""}`} />
          </button>
          <DownloadLink
            className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100"
            title="导出 Markdown"
            path={apiWrongbook.mdUrl(w.id)}
            fallbackName={`studyos-wrong-${w.id}.md`}
          >
            <Download size={14} />
          </DownloadLink>
          <button
            className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100"
            title="编辑"
            onClick={onEdit}
          >
            <Pencil size={14} />
          </button>
          <button
            className="rounded-lg p-1.5 text-slate-400 hover:bg-red-50 hover:text-red-500"
            title="删除"
            onClick={onDelete}
          >
            <Trash2 size={14} />
          </button>
        </div>
      </div>

      {isOpen && (
        <div className="mt-3 border-t border-slate-100 pt-3">
          {isPracticing ? (
            <div className="rounded-xl bg-rose-50/60 p-3">
              <div className="text-[11px] font-semibold text-rose-500">重练 · 先回忆再看答案</div>
              <div className="mt-1 whitespace-pre-wrap text-sm leading-6 text-slate-800">
                {w.question || w.title}
              </div>
              {!revealed ? (
                <button
                  onClick={() => setRevealed(true)}
                  className="mt-2 flex items-center gap-1 rounded-lg bg-rose-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-rose-700"
                >
                  <Eye size={13} /> 显示答案
                </button>
              ) : (
                <>
                  {section("正确答案与解析", "text-emerald-600", w.answer)}
                  {section("错因分析", "text-slate-500", w.reason)}
                  <div className="mt-3 flex items-center gap-2">
                    <button
                      onClick={() => grade(false)}
                      disabled={grading}
                      className="flex items-center gap-1 rounded-lg border border-red-200 bg-white px-3 py-1.5 text-xs font-medium text-red-600 hover:bg-red-50 disabled:opacity-50"
                    >
                      <X size={13} /> 又错了
                    </button>
                    <button
                      onClick={() => grade(true)}
                      disabled={grading}
                      className="flex items-center gap-1 rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
                    >
                      <Check size={13} /> 答对了
                    </button>
                    <span className="ml-1 text-[11px] text-slate-400">
                      连续答对 2 次 → 已掌握{grading ? "，提交中…" : ""}
                    </span>
                  </div>
                </>
              )}
            </div>
          ) : (
            <>
              {section("题目", "text-slate-500", w.question)}
              {section("我的答案", "text-red-500", w.my_answer)}
              {section("正确答案与解析", "text-emerald-600", w.answer)}
              {section("错因分析", "text-amber-600", w.reason)}
            </>
          )}
        </div>
      )}
    </div>
  );
}

/** 重练模式：随机抽「待攻克」错题逐题作答 */
function PracticeModal({
  courseId,
  onClose,
  onFinished,
}: {
  courseId?: number;
  onClose: () => void;
  onFinished: () => void;
}) {
  const [queue, setQueue] = useState<WrongQuestion[]>([]);
  const [idx, setIdx] = useState(0);
  const [revealed, setRevealed] = useState(false);
  const [okCount, setOkCount] = useState(0);
  const [missCount, setMissCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [finished, setFinished] = useState(false);

  useEffect(() => {
    apiWrongbook
      .queue({ course_id: courseId, limit: 10 })
      .then((qs) => setQueue(qs))
      .catch((e) => alert((e as Error).message))
      .finally(() => setLoading(false));
  }, [courseId]);

  const current = queue[idx];

  const grade = async (ok: boolean) => {
    if (!current || submitting) return;
    setSubmitting(true);
    try {
      await apiWrongbook.practice(current.id, ok);
      if (ok) setOkCount((n) => n + 1);
      else setMissCount((n) => n + 1);
      setRevealed(false);
      if (idx + 1 >= queue.length) {
        setFinished(true);
        onFinished();
      } else {
        setIdx((i) => i + 1);
      }
    } catch (e) {
      alert((e as Error).message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal title="重练模式" onClose={onClose} wide>
      {loading ? (
        <div className="flex items-center gap-2 py-6 text-sm text-slate-400">
          <Spinner /> 正在抽题…
        </div>
      ) : queue.length === 0 ? (
        <div className="py-6 text-center text-sm text-slate-400">
          {courseId != null ? "这门课没有待攻克的错题" : "没有待攻克的错题——全部掌握了 🎉"}
        </div>
      ) : finished ? (
        <div className="py-6 text-center">
          <div className="text-2xl">🎉</div>
          <div className="mt-2 text-sm font-semibold text-slate-700">本组重练完成</div>
          <div className="mt-1 text-xs text-slate-500">
            答对 <span className="font-semibold text-emerald-600">{okCount}</span> 题 · 又错{" "}
            <span className="font-semibold text-red-500">{missCount}</span> 题
          </div>
          <div className="mt-4 flex justify-center gap-2">
            <button
              onClick={() => {
                setIdx(0); setOkCount(0); setMissCount(0); setFinished(false); setLoading(true);
                apiWrongbook
                  .queue({ course_id: courseId, limit: 10 })
                  .then(setQueue)
                  .finally(() => setLoading(false));
              }}
              className="rounded-xl bg-violet-600 px-4 py-2 text-xs font-medium text-white hover:bg-violet-700"
            >
              再练一组
            </button>
            <button
              onClick={onClose}
              className="rounded-xl border border-slate-200 bg-white px-4 py-2 text-xs font-medium text-slate-600 hover:bg-slate-50"
            >
              完成
            </button>
          </div>
        </div>
      ) : (
        <div>
          <div className="mb-3 flex items-center justify-between text-xs text-slate-400">
            <span>
              第 {idx + 1} / {queue.length} 题
            </span>
            <span>
              答对 {okCount} · 又错 {missCount}
            </span>
          </div>
          <div className="mb-2 flex flex-wrap items-center gap-2">
            {current.course_name && (
              <span className="rounded-md bg-brand-50 px-1.5 py-0.5 text-[10px] font-medium text-brand-600">
                {current.course_name}
              </span>
            )}
            {current.chapter && (
              <span className="rounded-md bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-500">
                {current.chapter}
              </span>
            )}
            {current.difficulty && (
              <span className={`rounded-md px-1.5 py-0.5 text-[10px] font-medium ${DIFF_COLORS[current.difficulty]}`}>
                {DIFFICULTIES[current.difficulty]}
              </span>
            )}
            {current.source && <span className="text-[11px] text-slate-400">来源：{current.source}</span>}
          </div>
          <div className="rounded-xl border border-slate-200 bg-slate-50/60 p-4">
            <div className="text-xs font-semibold text-slate-500">{current.title}</div>
            <div className="mt-2 whitespace-pre-wrap text-sm leading-6 text-slate-800">
              {current.question || current.title}
            </div>
          </div>
          {!revealed ? (
            <button
              onClick={() => setRevealed(true)}
              className="mt-3 flex items-center gap-1.5 rounded-xl bg-violet-600 px-4 py-2 text-sm font-medium text-white hover:bg-violet-700"
            >
              <Eye size={15} /> 显示答案
            </button>
          ) : (
            <div className="mt-3 space-y-3">
              <div className="rounded-xl border border-emerald-100 bg-emerald-50/60 p-3">
                <div className="text-[11px] font-semibold text-emerald-600">正确答案与解析</div>
                <div className="mt-1 whitespace-pre-wrap text-sm leading-6 text-slate-700">
                  {current.answer || "（未填写）"}
                </div>
              </div>
              {current.reason && (
                <div className="rounded-xl border border-amber-100 bg-amber-50/60 p-3">
                  <div className="text-[11px] font-semibold text-amber-600">错因分析</div>
                  <div className="mt-1 whitespace-pre-wrap text-sm leading-6 text-slate-700">
                    {current.reason}
                  </div>
                </div>
              )}
              <div className="flex items-center gap-2">
                <button
                  onClick={() => grade(false)}
                  disabled={submitting}
                  className="flex items-center gap-1.5 rounded-xl border border-red-200 bg-white px-4 py-2 text-sm font-medium text-red-600 hover:bg-red-50 disabled:opacity-50"
                >
                  <X size={15} /> 又错了
                </button>
                <button
                  onClick={() => grade(true)}
                  disabled={submitting}
                  className="flex items-center gap-1.5 rounded-xl bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
                >
                  <Check size={15} /> 答对了
                </button>
                <button
                  onClick={() => setRevealed(false)}
                  className="flex items-center gap-1 rounded-xl px-2 py-2 text-xs text-slate-400 hover:text-slate-600"
                  title="收起答案"
                >
                  <EyeOff size={14} />
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </Modal>
  );
}

/** 新建 / 编辑错题表单 */
function WrongForm({
  initial,
  courses,
  onDone,
}: {
  initial: WrongQuestion | null;
  courses: Course[];
  onDone: () => void;
}) {
  const [form, setForm] = useState({
    course_id: initial?.course_id ?? (null as number | null),
    title: initial?.title ?? "",
    source: initial?.source ?? "",
    chapter: initial?.chapter ?? "",
    difficulty: initial?.difficulty ?? "",
    tags: initial?.tags.join("，") ?? "",
    question: initial?.question ?? "",
    my_answer: initial?.my_answer ?? "",
    answer: initial?.answer ?? "",
    reason: initial?.reason ?? "",
    status: initial?.status ?? "open",
  });
  const [saving, setSaving] = useState(false);

  // AI 提炼：粘贴错题原文 → 自动预填表单（只填空字段，不覆盖已填内容）
  const [raw, setRaw] = useState("");
  const [refining, setRefining] = useState(false);
  const [refineMsg, setRefineMsg] = useState("");
  const [refineErr, setRefineErr] = useState("");
  const [refineEngine, setRefineEngine] = useState("");

  const set = (k: string, v: unknown) => setForm((f) => ({ ...f, [k]: v }));

  const refine = async () => {
    if (!raw.trim() || refining) return;
    setRefining(true);
    setRefineMsg("");
    setRefineErr("");
    setRefineEngine("");
    try {
      const r = await apiWrongbook.refine(raw);
      setForm((f) => ({
        ...f,
        title: f.title || r.title,
        question: f.question || r.question,
        my_answer: f.my_answer || r.my_answer,
        answer: f.answer || r.answer,
        reason: f.reason || r.reason,
        chapter: f.chapter || r.chapter,
        difficulty: f.difficulty || r.difficulty,
        tags: f.tags || r.tags.join("，"),
      }));
      setRefineEngine(r.engine);
      setRefineMsg("已提炼填入表单，请核对后再保存");
    } catch (e) {
      setRefineErr((e as Error).message);
    } finally {
      setRefining(false);
    }
  };

  const save = async () => {
    if (!form.title.trim() && !form.question.trim()) {
      alert("请至少填写标题或题目");
      return;
    }
    setSaving(true);
    try {
      const payload = {
        course_id: form.course_id,
        title: form.title.trim() || form.question.trim().slice(0, 60),
        question: form.question,
        my_answer: form.my_answer,
        answer: form.answer,
        reason: form.reason,
        source: form.source,
        chapter: form.chapter,
        difficulty: form.difficulty,
        tags: form.tags.split(/[，,]/).map((t) => t.trim()).filter(Boolean),
      };
      if (initial) {
        await apiWrongbook.patch(initial.id, { ...payload, status: form.status });
      } else {
        await apiWrongbook.create(payload);
      }
      onDone();
    } catch (e) {
      alert((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const field = (
    label: string,
    node: ReactNode,
    hint?: string,
  ) => (
    <label className="block text-xs text-slate-500">
      {label}
      {node}
      {hint && <div className="mt-0.5 text-[10px] text-slate-300">{hint}</div>}
    </label>
  );

  const inputCls = "mt-1 w-full rounded-xl border border-slate-200 px-3 py-2 text-sm outline-none transition focus:border-brand-500";

  return (
    <div className="space-y-3">
      {/* AI 提炼：粘贴错题原文，自动预填表单 */}
      <div className="rounded-xl border border-violet-200 bg-violet-50/50 p-3">
        <div className="flex items-center gap-1.5 text-xs font-semibold text-violet-600">
          <Wand2 size={13} /> AI 提炼 · 粘贴错题原文，自动补全表单
        </div>
        <textarea
          rows={3}
          className="mt-2 w-full resize-none rounded-xl border border-violet-200 bg-white px-3 py-2 text-sm outline-none transition placeholder:text-slate-300 focus:border-violet-400"
          placeholder="把题目、你当时的答案、参考答案/解析一起粘进来，AI 自动填写标题 / 题目 / 答案 / 错因 / 章节 / 难度 / 标签（可再手动修改）"
          value={raw}
          onChange={(e) => setRaw(e.target.value)}
        />
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <button
            onClick={refine}
            disabled={refining || !raw.trim()}
            className="flex items-center gap-1.5 rounded-xl bg-violet-600 px-4 py-2 text-xs font-medium text-white transition hover:bg-violet-700 disabled:opacity-50"
          >
            {refining ? <Spinner /> : <Wand2 size={13} />}
            开始提炼
          </button>
          {refineEngine && (
            <span
              className={`rounded-full px-2 py-0.5 text-[10px] ${
                refineEngine === "llm"
                  ? "bg-violet-100 text-violet-600"
                  : "bg-slate-200 text-slate-500"
              }`}
            >
              {refineEngine === "llm" ? "AI 模型提炼" : "离线规则提炼"}
            </span>
          )}
          {refineMsg && <span className="text-[11px] text-emerald-600">{refineMsg}</span>}
          {refineErr && <span className="text-[11px] text-red-600">{refineErr}</span>}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        {field("课程",
          <select className={inputCls} value={form.course_id ?? ""}
            onChange={(e) => set("course_id", e.target.value === "" ? null : Number(e.target.value))}>
            <option value="">未关联</option>
            {courses.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>)}
        {field("来源",
          <input className={inputCls} placeholder="如：2024 期末试卷"
            value={form.source} onChange={(e) => set("source", e.target.value)} />)}
        {field("章节",
          <input className={inputCls} placeholder="留空自动识别"
            value={form.chapter} onChange={(e) => set("chapter", e.target.value)} />,
          "按「标题 + 题干」自动抽取")}
        {field("难度",
          <select className={inputCls} value={form.difficulty}
            onChange={(e) => set("difficulty", e.target.value)}>
            {Object.entries(DIFFICULTIES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>)}
      </div>
      {field("标题",
        <input className={inputCls} placeholder="一句话概括这道题"
          value={form.title} onChange={(e) => set("title", e.target.value)} />)}
      {field("标签",
        <input className={inputCls} placeholder="逗号分隔，如：死锁，银行家"
          value={form.tags} onChange={(e) => set("tags", e.target.value)} />)}
      {field("题目",
        <textarea rows={3} className={`${inputCls} resize-none`} placeholder="题目原文（可粘贴图片 OCR 后的文本）"
          value={form.question} onChange={(e) => set("question", e.target.value)} />)}
      <div className="grid gap-3 md:grid-cols-2">
        {field("我的答案（当时怎么错的）",
          <textarea rows={2} className={`${inputCls} resize-none`}
            value={form.my_answer} onChange={(e) => set("my_answer", e.target.value)} />)}
        {field("正确答案与解析",
          <textarea rows={2} className={`${inputCls} resize-none`}
            value={form.answer} onChange={(e) => set("answer", e.target.value)} />)}
      </div>
      {field("错因分析",
        <textarea rows={2} className={`${inputCls} resize-none`} placeholder="如：漏算了一个资源 / 概念记混"
          value={form.reason} onChange={(e) => set("reason", e.target.value)} />)}
      {initial && field("掌握状态",
        <select className={inputCls} value={form.status} onChange={(e) => set("status", e.target.value)}>
          <option value="open">待攻克</option>
          <option value="done">已掌握</option>
        </select>)}
      <button
        onClick={save}
        disabled={saving}
        className="w-full rounded-xl bg-brand-600 py-2.5 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-50"
      >
        {saving ? "保存中…" : initial ? "保存修改" : "归集这道错题"}
      </button>
    </div>
  );
}
