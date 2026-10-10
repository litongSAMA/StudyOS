import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  BrainCircuit,
  Check,
  ChevronDown,
  Download,
  Eye,
  EyeOff,
  ListChecks,
  RotateCcw,
  Trash2,
  Wand2,
  X,
} from "lucide-react";
import { apiCourses, apiMaterials, apiNotes, apiQuiz } from "../api";
import { useApp } from "../App";
import DownloadLink from "../components/DownloadLink";
import { Empty, Modal, PageHeader, Spinner } from "../components/ui";
import { QUIZ_TYPES, QUIZ_VERDICTS } from "../types";
import type { Course, Material, Note, Quiz, QuizQuestion, QuizStats } from "../types";
import { fmtDT } from "../utils";

const LETTERS = "ABCDEFGH";

/** 出题来源 */
const SOURCES = [
  { value: "course", label: "按课程" },
  { value: "material", label: "按资料" },
  { value: "notes", label: "按笔记" },
  { value: "text", label: "自由文本" },
] as const;

const NS = [3, 5, 8, 10];

export default function QuizPage() {
  const { refreshKey } = useApp();
  const [searchParams] = useSearchParams();
  const [courses, setCourses] = useState<Course[]>([]);
  const [materials, setMaterials] = useState<Material[]>([]);
  const [notes, setNotes] = useState<Note[]>([]);
  const [stats, setStats] = useState<QuizStats | null>(null);
  const [items, setItems] = useState<Quiz[]>([]);
  const [loading, setLoading] = useState(false);

  // 出题面板状态
  const [source, setSource] = useState<string>("course");
  const [courseId, setCourseId] = useState<number | "">("");
  const [materialId, setMaterialId] = useState<number | "">("");
  const [selNotes, setSelNotes] = useState<number[]>([]);
  const [text, setText] = useState("");
  const [qtype, setQtype] = useState("mixed");
  const [n, setN] = useState(5);
  const [generating, setGenerating] = useState(false);

  // 正在自测 / 查看的组
  const [running, setRunning] = useState<Quiz | null>(null);
  const [viewing, setViewing] = useState<Quiz | null>(null);

  const refreshAll = async () => {
    setStats(await apiQuiz.stats());
  };

  const loadList = async () => {
    setLoading(true);
    try {
      const r = await apiQuiz.list({ limit: 100 });
      setItems(r.items);
    } catch (e) {
      alert((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    apiCourses.list().then(setCourses).catch(console.error);
    apiMaterials.list({ limit: 500 }).then((p) => setMaterials(p.items)).catch(console.error);
    apiNotes.list({ limit: 500 }).then(setNotes).catch(console.error);
    refreshAll().catch(console.error);
  }, [refreshKey]);

  useEffect(() => {
    loadList();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshKey]);

  // 笔记/资料页「抽背」按钮跳转：?notes=1,2 / ?material=3 预设来源
  useEffect(() => {
    const qn = searchParams.get("notes");
    const qm = searchParams.get("material");
    if (qn) {
      const ids = qn.split(",").map(Number).filter((x) => Number.isFinite(x) && x > 0);
      if (ids.length) {
        setSource("notes");
        setSelNotes(ids);
      }
    } else if (qm) {
      const id = Number(qm);
      if (Number.isFinite(id) && id > 0) {
        setSource("material");
        setMaterialId(id);
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const afterMutation = () => {
    loadList();
    refreshAll();
  };

  const generate = async () => {
    if (generating) return;
    const payload: Record<string, unknown> = { qtype, n };
    if (source === "course") {
      if (courseId === "") return alert("请先选择课程");
      payload.course_id = courseId;
    } else if (source === "material") {
      if (materialId === "") return alert("请先选择资料");
      payload.material_id = materialId;
    } else if (source === "notes") {
      if (!selNotes.length) return alert("请至少勾选一条笔记");
      payload.note_ids = selNotes;
    } else {
      if (!text.trim()) return alert("请粘贴要出题的文本");
      payload.text = text.trim();
    }
    setGenerating(true);
    try {
      const q = await apiQuiz.create(payload as Parameters<typeof apiQuiz.create>[0]);
      if (!q.questions.length) {
        alert("未能从该来源出题：文本太短或没有可考的句子");
      } else {
        setRunning(q);
        afterMutation();
      }
    } catch (e) {
      alert((e as Error).message);
    } finally {
      setGenerating(false);
    }
  };

  const remove = async (q: Quiz) => {
    if (!confirm(`删除自测「${q.title}」？`)) return;
    await apiQuiz.remove(q.id);
    if (running?.id === q.id) setRunning(null);
    afterMutation();
  };

  return (
    <div className="space-y-5">
      <PageHeader
        title="AI 自测 / 抽背"
        desc="基于笔记 / 资料全文自动出选择题与问答题——选择题自动判分，问答题先回忆再看参考答案自评"
        actions={
          <DownloadLink
            path={apiQuiz.mdUrl()}
            fallbackName="studyos-quiz.md"
            className="flex items-center gap-1.5 rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs font-medium text-slate-600 transition hover:border-brand-300 hover:text-brand-600"
          >
            <Download size={14} /> 导出 Markdown
          </DownloadLink>
        }
      />

      {/* 统计卡片 */}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        {[
          { label: "自测组数", value: stats?.total ?? "–", cls: "text-slate-700" },
          { label: "已答题目", value: stats?.answered ?? "–", cls: "text-brand-600" },
          { label: "选择题正确率", value: stats?.choice_answered ? `${stats.choice_pct}%` : "–", cls: "text-emerald-600" },
          { label: "问答自评通过", value: stats?.qa_answered ? `${stats.qa_pct}%` : "–", cls: "text-violet-600" },
        ].map(({ label, value, cls }) => (
          <div key={label} className="rounded-2xl border border-slate-200 bg-white px-4 py-3 shadow-sm">
            <div className={`text-xl font-bold ${cls}`}>{value}</div>
            <div className="text-xs text-slate-400">{label}</div>
          </div>
        ))}
      </div>

      {/* 出题面板 */}
      <div className="rounded-2xl border border-violet-200 bg-violet-50/40 p-4 shadow-sm">
        <div className="mb-3 flex items-center gap-1.5 text-sm font-semibold text-violet-700">
          <Wand2 size={15} /> 出一组题，考考自己
        </div>
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs text-slate-500">出题来源</span>
            {SOURCES.map((s) => (
              <button
                key={s.value}
                onClick={() => setSource(s.value)}
                className={`rounded-lg px-2.5 py-1 text-xs font-medium transition ${
                  source === s.value ? "bg-violet-600 text-white" : "bg-white text-slate-500 border border-slate-200 hover:bg-violet-50"
                }`}
              >
                {s.label}
              </button>
            ))}
          </div>

          {source === "course" && (
            <div className="flex flex-wrap items-center gap-2">
              <select
                className="min-w-56 rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm outline-none focus:border-violet-400"
                value={courseId}
                onChange={(e) => setCourseId(e.target.value === "" ? "" : Number(e.target.value))}
              >
                <option value="">选择课程（自动用其笔记出题）</option>
                {courses.map((c) => (
                  <option key={c.id} value={c.id}>{c.name}</option>
                ))}
              </select>
              <span className="text-[11px] text-slate-400">
                优先用该课程笔记出题，无笔记时自动用资料全文
              </span>
            </div>
          )}

          {source === "material" && (
            <select
              className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm outline-none focus:border-violet-400"
              value={materialId}
              onChange={(e) => setMaterialId(e.target.value === "" ? "" : Number(e.target.value))}
            >
              <option value="">选择资料（用其全文出题）</option>
              {materials.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.title}{m.course_name ? `（${m.course_name}）` : ""}
                </option>
              ))}
            </select>
          )}

          {source === "notes" && (
            <div className="rounded-xl border border-slate-200 bg-white">
              <div className="flex items-center justify-between border-b border-slate-100 px-3 py-2">
                <span className="text-xs text-slate-500">勾选笔记（最多 10 条，可多选合并抽背）</span>
                {selNotes.length > 0 && (
                  <button
                    className="text-[11px] text-violet-600 hover:underline"
                    onClick={() => setSelNotes([])}
                  >
                    清空（已选 {selNotes.length}）
                  </button>
                )}
              </div>
              <div className="max-h-44 overflow-y-auto p-2">
                {notes.length === 0 ? (
                  <div className="p-3 text-center text-xs text-slate-400">
                    还没有笔记——先去「AI 笔记提炼」提炼几条，或改选其他来源
                  </div>
                ) : (
                  notes.map((nt) => {
                    const on = selNotes.includes(nt.id);
                    return (
                      <button
                        key={nt.id}
                        onClick={() =>
                          setSelNotes((prev) =>
                            on ? prev.filter((x) => x !== nt.id) : [...prev, nt.id].slice(0, 10),
                          )
                        }
                        className={`flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-xs transition ${
                          on ? "bg-violet-100 text-violet-700" : "text-slate-600 hover:bg-slate-50"
                        }`}
                      >
                        <span
                          className={`flex h-4 w-4 shrink-0 items-center justify-center rounded border ${
                            on ? "border-violet-600 bg-violet-600 text-white" : "border-slate-300 bg-white"
                          }`}
                        >
                          {on && <Check size={11} />}
                        </span>
                        <span className="min-w-0 flex-1 truncate">
                          {nt.title}
                          {nt.course_name && <span className="text-slate-400"> · {nt.course_name}</span>}
                        </span>
                        <span className="shrink-0 rounded bg-slate-100 px-1 py-0.5 text-[10px] text-slate-400">
                          {nt.mode === "exam" ? "考点" : "重点"}
                        </span>
                      </button>
                    );
                  })
                )}
              </div>
            </div>
          )}

          {source === "text" && (
            <textarea
              rows={4}
              className="w-full resize-none rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm outline-none transition placeholder:text-slate-300 focus:border-violet-400"
              placeholder="粘贴课堂笔记 / 讲义 / 教材段落，AI 自动出题（离线可用规则引擎出题）"
              value={text}
              onChange={(e) => setText(e.target.value)}
            />
          )}

          <div className="flex flex-wrap items-center gap-3">
            <div className="flex items-center gap-1.5">
              <span className="text-xs text-slate-500">题型</span>
              {(["choice", "qa", "mixed"] as const).map((t) => (
                <button
                  key={t}
                  onClick={() => setQtype(t)}
                  className={`rounded-lg px-2.5 py-1 text-xs font-medium transition ${
                    qtype === t ? "bg-violet-600 text-white" : "bg-white text-slate-500 border border-slate-200 hover:bg-violet-50"
                  }`}
                >
                  {QUIZ_TYPES[t]}
                </button>
              ))}
            </div>
            <div className="flex items-center gap-1.5">
              <span className="text-xs text-slate-500">题数</span>
              {NS.map((x) => (
                <button
                  key={x}
                  onClick={() => setN(x)}
                  className={`rounded-lg px-2.5 py-1 text-xs font-medium transition ${
                    n === x ? "bg-violet-600 text-white" : "bg-white text-slate-500 border border-slate-200 hover:bg-violet-50"
                  }`}
                >
                  {x}
                </button>
              ))}
            </div>
            <button
              onClick={generate}
              disabled={generating}
              className="ml-auto flex items-center gap-1.5 rounded-xl bg-violet-600 px-5 py-2 text-sm font-medium text-white transition hover:bg-violet-700 disabled:opacity-50"
            >
              {generating ? <Spinner /> : <BrainCircuit size={15} />}
              {generating ? "AI 出题中…" : "生成自测"}
            </button>
          </div>
        </div>
      </div>

      {/* 历史自测 */}
      <div>
        <div className="mb-2 text-xs text-slate-400">
          历史自测（可继续作答 / 重做）· 共 {stats?.total ?? 0} 组
        </div>
        {loading ? (
          <div className="flex items-center gap-2 text-sm text-slate-400">
            <Spinner /> 加载中…
          </div>
        ) : items.length === 0 ? (
          <Empty text="还没有自测记录——在上面选个来源，生成第一组题吧" />
        ) : (
          <div className="space-y-3">
            {items.map((q) => (
              <QuizCard
                key={q.id}
                q={q}
                onOpen={() => setRunning(q)}
                onView={() => setViewing(q)}
                onDelete={() => remove(q)}
              />
            ))}
          </div>
        )}
      </div>

      {/* 自测答题器 */}
      {running && (
        <QuizRunner
          quiz={running}
          onUpdate={(q) => {
            setRunning(q);
            afterMutation();
          }}
          onClose={() => setRunning(null)}
          onFinished={afterMutation}
        />
      )}

      {/* 查看历史（题目+答案+作答） */}
      {viewing && (
        <Modal title={viewing.title} onClose={() => setViewing(null)} wide>
          <QuizReview quiz={viewing} onRedo={() => { setRunning(viewing); setViewing(null); }} />
        </Modal>
      )}
    </div>
  );
}

/** 历史卡片：题型/引擎/来源徽章 + 得分环 */
function QuizCard({
  q,
  onOpen,
  onView,
  onDelete,
}: {
  q: Quiz;
  onOpen: () => void;
  onView: () => void;
  onDelete: () => void;
}) {
  const done = q.answered >= q.total;
  const scoreCls =
    q.answered === 0
      ? "text-slate-400"
      : q.score_pct >= 80
        ? "text-emerald-600"
        : q.score_pct >= 60
          ? "text-amber-600"
          : "text-red-500";
  return (
    <div className="group rounded-2xl border border-slate-200 bg-white p-4 shadow-sm transition hover:border-violet-200">
      <div className="flex items-start gap-3">
        <div className="mt-0.5 rounded-lg bg-violet-100 p-2 text-violet-600">
          <BrainCircuit size={15} />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-sm font-semibold text-slate-800">{q.title}</h3>
            <span className="rounded-md bg-violet-100 px-1.5 py-0.5 text-[10px] font-medium text-violet-600">
              {QUIZ_TYPES[q.qtype] ?? q.qtype}
            </span>
            <span
              className={`rounded-md px-1.5 py-0.5 text-[10px] font-medium ${
                q.engine === "llm" ? "bg-violet-100 text-violet-600" : "bg-slate-100 text-slate-500"
              }`}
            >
              {q.engine === "llm" ? "AI 出题" : "规则出题"}
            </span>
            {done && (
              <span className="rounded-md bg-emerald-100 px-1.5 py-0.5 text-[10px] font-medium text-emerald-600">
                已完成
              </span>
            )}
          </div>
          <div className="mt-0.5 flex flex-wrap items-center gap-x-2 text-[11px] text-slate-400">
            {q.course_name && <span>{q.course_name}</span>}
            {q.material_title && <span>来源：{q.material_title}</span>}
            {q.note_ids.length > 0 && <span>基于 {q.note_ids.length} 条笔记</span>}
            <span>{fmtDT(q.created_at)}</span>
          </div>
        </div>
        <div className="shrink-0 text-right">
          <div className={`text-lg font-bold ${scoreCls}`}>
            {q.answered === 0 ? "–" : `${q.correct}/${q.answered}`}
          </div>
          <div className="text-[10px] text-slate-400">
            {q.answered === 0 ? "未作答" : `${q.score_pct}% · 共 ${q.total} 题`}
          </div>
        </div>
        <div className="flex shrink-0 gap-1 opacity-0 transition group-hover:opacity-100">
          <button
            className="rounded-lg p-1.5 text-slate-400 hover:bg-violet-50 hover:text-violet-600"
            title={done ? "重做这组" : "继续自测"}
            onClick={onOpen}
          >
            {done ? <RotateCcw size={14} /> : <ListChecks size={14} />}
          </button>
          <button
            className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100"
            title="查看题目与作答"
            onClick={onView}
          >
            <ChevronDown size={14} />
          </button>
          <DownloadLink
            className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100"
            title="导出 Markdown"
            path={apiQuiz.mdUrl(q.id)}
            fallbackName={`studyos-quiz-${q.id}.md`}
          >
            <Download size={14} />
          </DownloadLink>
          <button
            className="rounded-lg p-1.5 text-slate-400 hover:bg-red-50 hover:text-red-500"
            title="删除"
            onClick={onDelete}
          >
            <Trash2 size={14} />
          </button>
        </div>
      </div>
    </div>
  );
}

/** 答题器：逐题作答（选择题自动判分 / 问答题自评），结束后汇总复盘 */
function QuizRunner({
  quiz,
  onUpdate,
  onClose,
  onFinished,
}: {
  quiz: Quiz;
  onUpdate: (q: Quiz) => void;
  onClose: () => void;
  onFinished: () => void;
}) {
  const [idx, setIdx] = useState(() => {
    const answeredSet = new Set(quiz.results.map((r) => r.index));
    for (let i = 0; i < quiz.questions.length; i++) if (!answeredSet.has(i)) return i;
    return Math.max(0, quiz.questions.length - 1);
  });
  const [revealed, setRevealed] = useState(false);
  const [myText, setMyText] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [feedback, setFeedback] = useState<Quiz | null>(null);
  // 已全部作答的组继续打开 → 直接进结果页
  const [finished, setFinished] = useState(
    () => quiz.total > 0 && quiz.answered >= quiz.total,
  );

  const total = quiz.questions.length;
  const current: QuizQuestion | undefined = quiz.questions[idx];
  const resultOf = (i: number) => quiz.results.find((r) => r.index === i);

  // 重新作答按钮：重置服务端作答 + 回到第 1 题
  const restart = async () => {
    try {
      const q = await apiQuiz.reset(quiz.id);
      onUpdate(q);
      setIdx(0);
      setRevealed(false);
      setMyText("");
      setFeedback(null);
      setFinished(false);
    } catch (e) {
      alert((e as Error).message);
    }
  };

  const advance = () => {
    const next = idx + 1;
    setRevealed(false);
    setMyText("");
    setFeedback(null);
    if (next >= total) {
      setFinished(true);
    } else {
      setIdx(next);
    }
  };

  const submit = async (payload: { index: number; choice?: string; answer_text?: string; ok?: boolean }) => {
    if (submitting) return;
    setSubmitting(true);
    try {
      const q = await apiQuiz.answer(quiz.id, payload);
      onUpdate(q);
      setFeedback(q);
      onFinished();
    } catch (e) {
      alert((e as Error).message);
    } finally {
      setSubmitting(false);
    }
  };

  if (finished) {
    const answeredSet = quiz.results.filter((r) => r.index >= 0 && r.index < total);
    return (
      <Modal title="自测完成" onClose={onClose} wide>
        <div className="text-center">
          <div className="text-3xl">🎉</div>
          <div className="mt-2 text-base font-semibold text-slate-700">
            {quiz.score_pct >= 80 ? "掌握得很扎实！" : quiz.score_pct >= 60 ? "还不错，错题再补补" : "再复习一下来源笔记吧"}
          </div>
          <div className="mt-1 text-sm text-slate-500">
            答对 <span className="font-semibold text-emerald-600">{quiz.correct}</span> / 已答{" "}
            {quiz.answered} 题{quiz.total ? `（共 ${quiz.total} 题）` : ""} · {quiz.score_pct}%
          </div>
          <div className="mt-4 grid gap-2 text-left">
            {quiz.questions.map((question, i) => {
              const r = answeredSet.find((x) => x.index === i);
              const v = r ? QUIZ_VERDICTS[r.verdict] : null;
              return (
                <div key={i} className="flex items-start gap-2 rounded-xl border border-slate-100 bg-slate-50/60 p-2.5">
                  <span
                    className={`mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[10px] font-bold ${
                      !r
                        ? "bg-slate-200 text-slate-500"
                        : r.verdict === "correct" || r.verdict === "self_ok"
                          ? "bg-emerald-500 text-white"
                          : "bg-red-500 text-white"
                    }`}
                  >
                    {!r ? "–" : r.verdict === "correct" || r.verdict === "self_ok" ? <Check size={11} /> : <X size={11} />}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="text-xs font-medium text-slate-700">
                      <span className="mr-1 text-slate-400">{i + 1}.</span>
                      {question.stem}
                    </div>
                    <div className="mt-0.5 text-[11px] text-slate-400">
                      {question.type === "choice" ? "答案：" : "参考答案："}
                      {question.type === "choice" ? `${question.answer}. ${question.options[question.answer_index]}` : question.answer}
                      {v && <span className={`ml-2 ${v.cls}`}>· {v.label}</span>}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
          <div className="mt-5 flex justify-center gap-2">
            <button
              onClick={restart}
              className="flex items-center gap-1.5 rounded-xl bg-violet-600 px-4 py-2 text-xs font-medium text-white hover:bg-violet-700"
            >
              <RotateCcw size={13} /> 重新作答
            </button>
            <button
              onClick={onClose}
              className="rounded-xl border border-slate-200 bg-white px-4 py-2 text-xs font-medium text-slate-600 hover:bg-slate-50"
            >
              完成
            </button>
          </div>
        </div>
      </Modal>
    );
  }

  if (!current) {
    return (
      <Modal title="自测" onClose={onClose} wide>
        <div className="py-6 text-center text-sm text-slate-400">这组自测没有题目</div>
      </Modal>
    );
  }

  const prevResult = resultOf(idx);
  const fbResult = feedback?.results.find((r) => r.index === idx);
  const isChoice = current.type === "choice";

  return (
    <Modal title={quiz.title} onClose={onClose} wide>
      <div className="mb-3 flex items-center justify-between text-xs text-slate-400">
        <span>
          第 {idx + 1} / {total} 题 · {QUIZ_TYPES[current.type]}
        </span>
        <span>
          已答 {quiz.answered} · 答对 <span className="font-semibold text-emerald-600">{quiz.correct}</span>
        </span>
      </div>
      <div className="mb-2 h-1.5 overflow-hidden rounded-full bg-slate-100">
        <div
          className="h-full rounded-full bg-violet-500 transition-all"
          style={{ width: `${((idx + (fbResult ? 1 : 0)) / Math.max(total, 1)) * 100}%` }}
        />
      </div>
      {quiz.course_name && (
        <span className="mb-2 inline-block rounded-md bg-brand-50 px-1.5 py-0.5 text-[10px] font-medium text-brand-600">
          {quiz.course_name}
        </span>
      )}

      <div className="rounded-xl border border-slate-200 bg-slate-50/60 p-4">
        <div className="whitespace-pre-wrap text-sm leading-6 text-slate-800">{current.stem}</div>
      </div>

      {/* 选择题 */}
      {isChoice && (
        <div className="mt-3 space-y-2">
          {current.options.map((opt, i) => {
            const letter = LETTERS[i];
            const isAnswer = fbResult && letter === current.answer;
            const isChosen = fbResult && fbResult.chosen === letter;
            let cls = "border-slate-200 bg-white hover:border-violet-300";
            if (fbResult) {
              if (isAnswer) cls = "border-emerald-400 bg-emerald-50";
              else if (isChosen) cls = "border-red-300 bg-red-50";
              else cls = "border-slate-200 bg-white opacity-60";
            } else if (prevResult && prevResult.chosen === letter && !revealed) {
              cls = "border-violet-300 bg-violet-50";
            }
            return (
              <button
                key={i}
                disabled={!!fbResult || submitting}
                onClick={() => submit({ index: idx, choice: letter })}
                className={`flex w-full items-start gap-2.5 rounded-xl border px-3.5 py-2.5 text-left text-sm transition disabled:cursor-default ${cls}`}
              >
                <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full border border-slate-300 text-[10px] font-bold text-slate-500">
                  {letter}
                </span>
                <span className="whitespace-pre-wrap text-slate-700">{opt}</span>
              </button>
            );
          })}
        </div>
      )}

      {/* 问答题：先回忆 → 显示参考答案 → 自评 */}
      {!isChoice && (
        <div className="mt-3 space-y-3">
          {!revealed && !fbResult ? (
            <div>
              <textarea
                rows={3}
                className="w-full resize-none rounded-xl border border-slate-200 px-3 py-2 text-sm outline-none transition placeholder:text-slate-300 focus:border-violet-400"
                placeholder="先在脑子里回忆，也可以把答案默写在这里（可不填）…"
                value={myText}
                onChange={(e) => setMyText(e.target.value)}
              />
              <button
                onClick={() => setRevealed(true)}
                className="mt-2 flex items-center gap-1.5 rounded-xl bg-violet-600 px-4 py-2 text-sm font-medium text-white hover:bg-violet-700"
              >
                <Eye size={15} /> 显示参考答案
              </button>
            </div>
          ) : (
            <>
              <div className="rounded-xl border border-emerald-100 bg-emerald-50/60 p-3">
                <div className="text-[11px] font-semibold text-emerald-600">参考答案</div>
                <div className="mt-1 whitespace-pre-wrap text-sm leading-6 text-slate-700">
                  {current.answer || "（无参考答案）"}
                </div>
                {current.explanation && (
                  <div className="mt-2 whitespace-pre-wrap text-xs leading-5 text-slate-500">
                    {current.explanation}
                  </div>
                )}
              </div>
              {!fbResult && (
                <div className="flex flex-wrap items-center gap-2">
                  <button
                    onClick={() => submit({ index: idx, ok: true, answer_text: myText })}
                    disabled={submitting}
                    className="flex items-center gap-1.5 rounded-xl bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
                  >
                    <Check size={15} /> 记住了
                  </button>
                  <button
                    onClick={() => submit({ index: idx, ok: false, answer_text: myText })}
                    disabled={submitting}
                    className="flex items-center gap-1.5 rounded-xl border border-red-200 bg-white px-4 py-2 text-sm font-medium text-red-600 hover:bg-red-50 disabled:opacity-50"
                  >
                    <X size={15} /> 没记住
                  </button>
                  <button
                    onClick={() => setRevealed(false)}
                    className="flex items-center gap-1 rounded-xl px-2 py-2 text-xs text-slate-400 hover:text-slate-600"
                    title="收起答案再想想"
                  >
                    <EyeOff size={14} />
                  </button>
                </div>
              )}
            </>
          )}
        </div>
      )}

      {/* 判分反馈（两题型共用） */}
      {fbResult && (
        <div className="mt-3 space-y-3">
          <div
            className={`rounded-xl p-3 text-sm font-medium ${
              fbResult.verdict === "correct" || fbResult.verdict === "self_ok"
                ? "border border-emerald-200 bg-emerald-50 text-emerald-700"
                : "border border-red-200 bg-red-50 text-red-600"
            }`}
          >
            {(fbResult.verdict === "correct" || fbResult.verdict === "self_ok" ? "✓ " : "✗ ") +
              (QUIZ_VERDICTS[fbResult.verdict]?.label ?? "")}
            {isChoice && fbResult.verdict === "wrong" && (
              <span className="ml-2 text-xs">
                正确答案：{current.answer}. {current.options[current.answer_index]}
              </span>
            )}
          </div>
          {current.explanation && (
            <div className="rounded-xl border border-slate-100 bg-slate-50/80 p-3 text-xs leading-5 text-slate-500">
              <span className="font-semibold text-slate-400">解析：</span>
              {current.explanation}
            </div>
          )}
          {!isChoice && fbResult.verdict === "self_wrong" && current.source && (
            <div className="rounded-xl border border-amber-100 bg-amber-50/60 p-3 text-xs leading-5 text-slate-600">
              <span className="font-semibold text-amber-600">出处原文：</span>
              {current.source}
            </div>
          )}
          <button
            onClick={advance}
            className="w-full rounded-xl bg-violet-600 py-2.5 text-sm font-medium text-white hover:bg-violet-700"
          >
            {idx + 1 >= total ? "查看本组结果" : "下一题"}
          </button>
        </div>
      )}
    </Modal>
  );
}

/** 只读复盘：题目 + 正确答案 + 我的作答 */
function QuizReview({
  quiz,
  onRedo,
}: {
  quiz: Quiz;
  onRedo: () => void;
}) {
  const resByIdx = new Map(quiz.results.map((r) => [r.index, r]));
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 text-xs text-slate-400">
        <span className={`rounded-md px-1.5 py-0.5 text-[10px] font-medium ${quiz.engine === "llm" ? "bg-violet-100 text-violet-600" : "bg-slate-100 text-slate-500"}`}>
          {quiz.engine === "llm" ? "AI 出题" : "离线规则出题"}
        </span>
        <span>{QUIZ_TYPES[quiz.qtype]}</span>
        {quiz.course_name && <span>{quiz.course_name}</span>}
        <span>{fmtDT(quiz.created_at)}</span>
        <span className="ml-auto">
          已答 {quiz.answered}/{quiz.total} · 得分 {quiz.score_pct}%
        </span>
      </div>
      {quiz.questions.map((question, i) => {
        const r = resByIdx.get(i);
        const v = r ? QUIZ_VERDICTS[r.verdict] : null;
        return (
          <div key={i} className="rounded-xl border border-slate-100 bg-slate-50/50 p-3">
            <div className="flex items-start gap-2">
              <span className="mt-0.5 text-xs font-bold text-slate-400">{i + 1}.</span>
              <div className="min-w-0 flex-1">
                <div className="whitespace-pre-wrap text-sm text-slate-800">{question.stem}</div>
                {question.type === "choice" ? (
                  <div className="mt-2 space-y-1">
                    {question.options.map((opt, j) => {
                      const letter = LETTERS[j];
                      const isAnswer = letter === question.answer;
                      const isChosen = r?.chosen === letter;
                      return (
                        <div
                          key={j}
                          className={`rounded-lg px-2.5 py-1.5 text-xs ${
                            isAnswer
                              ? "bg-emerald-50 font-medium text-emerald-700"
                              : isChosen
                                ? "bg-red-50 text-red-600"
                                : "text-slate-500"
                          }`}
                        >
                          {letter}. {opt}
                          {isAnswer && " ✅"}
                          {isChosen && " · 我的选择"}
                        </div>
                      );
                    })}
                  </div>
                ) : (
                  <div className="mt-2 rounded-lg bg-emerald-50/70 px-2.5 py-1.5 text-xs text-slate-600">
                    <span className="font-semibold text-emerald-600">参考答案：</span>
                    {question.answer}
                  </div>
                )}
                {r?.answer_text && (
                  <div className="mt-1.5 text-xs text-slate-500">
                    我的回忆：<span className="whitespace-pre-wrap">{r.answer_text}</span>
                  </div>
                )}
                {question.explanation && (
                  <div className="mt-1.5 text-xs leading-5 text-slate-400">解析：{question.explanation}</div>
                )}
                {v && <div className={`mt-1 text-[11px] font-medium ${v.cls}`}>· {v.label}</div>}
              </div>
            </div>
          </div>
        );
      })}
      <div className="flex justify-end gap-2">
        <button
          onClick={onRedo}
          className="flex items-center gap-1.5 rounded-xl bg-violet-600 px-4 py-2 text-xs font-medium text-white hover:bg-violet-700"
        >
          <RotateCcw size={13} /> 继续 / 重做
        </button>
      </div>
    </div>
  );
}
