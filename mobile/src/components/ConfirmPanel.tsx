import { useMemo, useRef, useState } from "react";
import { AlertTriangle, Check, FileText, ShieldCheck, Star } from "lucide-react";
import type {
  ApplyResult,
  ExtractResult,
} from "../types";
import { DIFFICULTIES, MATERIAL_TYPES, TASK_KINDS } from "../types";
import { isDeadlineChangeNotice, needsReview, prepareAgentApply, toIso, toLocalInput, updateMaterialTags, type TaskDraft, type MaterialDraft } from "../agent";
import { ApiHttpError, apiAgent } from "../api";
import { stage } from "../agentQueue";
import { loadPendingWrite } from "../agentWrites";
import { Link } from "react-router-dom";
import { Spinner } from "./ui";
import { useApp } from "../App";

interface Props {
  extract: ExtractResult;
  filePath?: string;
  fileName?: string;
  source?: string;
  textSource?: string;
  onApplied?: (result: ApplyResult) => void;
  onBusyChange?: (busy: boolean) => void;
  courses: string[];
}

function confBadge(c: number) {
  if (!Number.isFinite(c) || c > 1) return "bg-red-100 text-red-700";
  if (c >= 0.85) return "bg-emerald-100 text-emerald-700";
  if (c >= 0.6) return "bg-amber-100 text-amber-700";
  return "bg-red-100 text-red-700";
}

function confText(c: number) {
  if (!Number.isFinite(c) || c > 1) return "需核对";
  if (c >= 0.85) return "高置信";
  if (c >= 0.6) return "需核对";
  return "低置信";
}

export default function ConfirmPanel({
  extract,
  filePath = "",
  fileName = "",
  source = "text",
  textSource = "",
  onApplied,
  onBusyChange,
  courses,
}: Props) {
  const { bump } = useApp();
  const [tasks, setTasks] = useState<TaskDraft[]>(
    extract.tasks.map((t) => ({ ...t, keep: toIso(t.due_at) != null, reviewed: false }))
  );
  const [materials, setMaterials] = useState<MaterialDraft[]>(extract.materials.map((m) => ({ ...m, keep: true, reviewed: false })));
  const [tagInputs, setTagInputs] = useState(() => extract.materials.map((row) => row.tags.join("，")));
  const applyLock = useRef(false);
  const applied = useRef(false);
  const [applying, setApplying] = useState(false);
  const [queuedDone, setQueuedDone] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState<{ tasks: number; materials: number; reused: number } | null>(null);

  const courseList = useMemo(() => Array.from(new Set(courses)), [courses]);

  const selectedCount = tasks.filter((row) => row.keep).length + materials.filter((row) => row.keep).length;
  const reviewCount = [...tasks, ...materials].filter((row) => row.keep && needsReview(row.confidence) && !row.reviewed).length;
  const deadlineNotice = tasks.length > 0 && (isDeadlineChangeNotice(textSource) || tasks.some((row) => isDeadlineChangeNotice(row.source_text || "")));
  const noticeText = textSource || tasks.map((row) => row.source_text).filter(Boolean).join("\n");

  const apply = async () => {
    if (applyLock.current || applied.current || deadlineNotice) return;
    const prepared = prepareAgentApply(tasks, materials);
    if (prepared.error) {
      setError(prepared.error);
      return;
    }
    applyLock.current = true;
    onBusyChange?.(true);
    setApplying(true);
    setError("");
    try {
      const res = await apiAgent.apply({
        ...prepared.payload,
        file_path: filePath,
        file_name: fileName,
        source,
        text: textSource,
      });
      applied.current = true;
      setDone({ tasks: res.created_tasks.length, materials: res.created_materials.length, reused: (res.reused_tasks?.length ?? 0) + (res.reused_materials?.length ?? 0) });
      bump();
      onApplied?.(res);
    } catch (e) {
      setError(loadPendingWrite() && (!(e instanceof ApiHttpError) || e.status >= 500)
        ? "提交结果尚未确认，原请求已保存。请前往对话助手查询回执或使用原请求重试。"
        : (e as Error).message);
    } finally {
      applyLock.current = false;
      setApplying(false);
      onBusyChange?.(false);
    }
  };

  if (queuedDone) return <div role="status" className="rounded-xl bg-amber-50 p-4 text-sm text-amber-800">已保存到本机离线队列。<Link to="/assistant" className="ml-2 underline">联网后提交或迁移到其它设备</Link></div>;

  if (done) {
    return (
      <div className="flex items-center gap-3 rounded-2xl border border-emerald-200 bg-emerald-50 px-5 py-4 text-sm text-emerald-700">
        <ShieldCheck size={20} />
        已入库：{done.tasks} 条 DDL 任务、{done.materials} 份资料
        {done.tasks > 0 && "（已保存提醒设置）"}
        {done.reused > 0 && `，复用 ${done.reused} 项已有记录`}
      </div>
    );
  }

  return (
    <fieldset disabled={applying} className="min-w-0 space-y-4" aria-busy={applying}>
      <div className="rounded-xl bg-slate-50 px-4 py-3 text-xs text-slate-600" aria-live="polite">
        已选择 {tasks.filter((row) => row.keep).length} 条任务、{materials.filter((row) => row.keep).length} 份资料
        {reviewCount > 0 && <span className="ml-2 text-amber-700">{reviewCount} 项需核对</span>}
      </div>
      {/* 告警 */}
      {deadlineNotice && <div role="status" className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800">
        检测到截止日期变更通知，请更新原任务。
        <Link to="/assistant" state={{ message: noticeText }} className="ml-2 text-brand-700 underline">前往助手匹配原任务并预览更新</Link>
      </div>}
      {extract.warnings.length > 0 && (
        <div className="space-y-1.5 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-xs text-amber-700">
          {extract.warnings.map((w, i) => (
            <div key={i} className="flex items-start gap-1.5">
              <AlertTriangle size={14} className="mt-0.5 shrink-0" />
              {w}
            </div>
          ))}
        </div>
      )}

      {/* 任务建议 */}
      {tasks.length > 0 && (
        <div>
          <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">
            识别到 {tasks.length} 条 DDL 任务
          </div>
          <div className="space-y-2">
            {tasks.map((t, i) => (
              <div key={i} className="rounded-xl border border-slate-200 bg-white p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <input
                    type="checkbox"
                    aria-label={`选择任务 ${i + 1}`}
                    checked={t.keep}
                    onChange={(e) =>
                      setTasks((prev) =>
                        prev.map((x, j) => (j === i ? { ...x, keep: e.target.checked } : x))
                      )
                    }
                    className="h-4 w-4 accent-brand-600"
                  />
                  <input
                    className="min-w-0 flex-1 rounded-lg border border-slate-200 px-2.5 py-1.5 text-sm outline-none focus:border-brand-500"
                    aria-label={`任务 ${i + 1} 标题`}
                    value={t.title}
                    onChange={(e) =>
                      setTasks((prev) => prev.map((x, j) => (j === i ? { ...x, title: e.target.value } : x)))
                    }
                  />
                  <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${confBadge(t.confidence)}`}>
                    {confText(t.confidence)}
                  </span>
                </div>
                <div className="mt-2 flex flex-wrap items-center gap-2 pl-6">
                  <select
                    className="rounded-lg border border-slate-200 px-2 py-1 text-xs"
                    value={t.kind}
                    onChange={(e) =>
                      setTasks((prev) => prev.map((x, j) => (j === i ? { ...x, kind: e.target.value } : x)))
                    }
                  >
                    {Object.entries(TASK_KINDS).map(([k, v]) => (
                      <option key={k} value={k}>{v}</option>
                    ))}
                  </select>
                  <input
                    type="datetime-local"
                    className={`rounded-lg border px-2 py-1 text-xs ${
                      t.keep && !toIso(t.due_at) ? "border-red-400 bg-red-50" : "border-slate-200"
                    }`}
                    aria-label={`任务 ${i + 1} 截止时间`}
                    value={toLocalInput(t.due_at)}
                    onChange={(e) =>
                      setTasks((prev) =>
                        prev.map((x, j) => (j === i ? { ...x, due_at: toIso(e.target.value) } : x))
                      )
                    }
                  />
                  <input
                    list="course-options"
                    placeholder="课程（可空）"
                    className="w-36 rounded-lg border border-slate-200 px-2 py-1 text-xs"
                    value={t.course}
                    onChange={(e) =>
                      setTasks((prev) => prev.map((x, j) => (j === i ? { ...x, course: e.target.value } : x)))
                    }
                  />
                  {needsReview(t.confidence) && (
                    <label className="flex items-center gap-1 text-xs text-amber-700">
                      <input type="checkbox" checked={t.reviewed} onChange={(e) => setTasks((prev) => prev.map((row, j) => j === i ? { ...row, reviewed: e.target.checked } : row))} />
                      已核对标题、课程和截止时间
                    </label>
                  )}
                  {t.due_at && toIso(t.due_at) && new Date(t.due_at).getTime() < Date.now() && (
                    <span className="text-xs text-amber-700">截止时间已过去，请确认是否仍需入库</span>
                  )}
                  {t.due_expr && (
                    <span className="text-[10px] text-slate-400">原文：「{t.due_expr}」</span>
                  )}
                </div>
                {t.source_text && <details className="mt-2 pl-6 text-xs text-slate-500"><summary className="cursor-pointer">查看任务来源原文</summary><p className="mt-1 whitespace-pre-wrap break-words">{t.source_text}</p></details>}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 资料建议 */}
      {materials.length > 0 && (
        <div>
          <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">
            识别到 {materials.length} 份资料
          </div>
          <div className="space-y-2">
            {materials.map((m, i) => (
              <div key={i} className="rounded-xl border border-slate-200 bg-white p-3">
                <div className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    aria-label={`选择资料 ${i + 1}`}
                    checked={m.keep}
                    onChange={(e) =>
                      setMaterials((prev) =>
                        prev.map((x, j) => (j === i ? { ...x, keep: e.target.checked } : x))
                      )
                    }
                    className="h-4 w-4 accent-brand-600"
                  />
                  <FileText size={15} className="text-slate-400" />
                  <input
                    className="min-w-0 flex-1 rounded-lg border border-slate-200 px-2.5 py-1.5 text-sm outline-none focus:border-brand-500"
                    aria-label={`资料 ${i + 1} 标题`}
                    value={m.title}
                    onChange={(e) =>
                      setMaterials((prev) =>
                        prev.map((x, j) => (j === i ? { ...x, title: e.target.value } : x))
                      )
                    }
                  />
                  <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${confBadge(m.confidence)}`}>
                    {confText(m.confidence)}
                  </span>
                </div>
                <div className="mt-2 flex flex-wrap items-center gap-2 pl-8">
                  <select
                    className="rounded-lg border border-slate-200 px-2 py-1 text-xs"
                    value={m.type}
                    onChange={(e) =>
                      setMaterials((prev) =>
                        prev.map((x, j) => (j === i ? { ...x, type: e.target.value } : x))
                      )
                    }
                  >
                    {Object.entries(MATERIAL_TYPES).map(([k, v]) => (
                      <option key={k} value={k}>{v}</option>
                    ))}
                  </select>
                  <input
                    placeholder="章节（如：第3章 内存管理）"
                    className="w-44 rounded-lg border border-slate-200 px-2 py-1 text-xs"
                    value={m.chapter ?? ""}
                    onChange={(e) =>
                      setMaterials((prev) =>
                        prev.map((x, j) => (j === i ? { ...x, chapter: e.target.value } : x))
                      )
                    }
                  />
                  <select
                    className="rounded-lg border border-slate-200 px-2 py-1 text-xs"
                    value={m.difficulty ?? ""}
                    onChange={(e) =>
                      setMaterials((prev) =>
                        prev.map((x, j) => (j === i ? { ...x, difficulty: e.target.value } : x))
                      )
                    }
                  >
                    {Object.entries(DIFFICULTIES).map(([k, v]) => (
                      <option key={k} value={k}>{k === "" ? "难度" : v}</option>
                    ))}
                  </select>
                  <label
                    className={`flex cursor-pointer items-center gap-1 rounded-lg border px-2 py-1 text-xs transition ${
                      m.exam_focus
                        ? "border-red-300 bg-red-50 text-red-600"
                        : "border-slate-200 text-slate-500 hover:bg-red-50"
                    }`}
                  >
                    <input
                      type="checkbox"
                      className="h-3.5 w-3.5 accent-red-500"
                      checked={!!m.exam_focus}
                      onChange={(e) =>
                        setMaterials((prev) =>
                          prev.map((x, j) => (j === i ? { ...x, exam_focus: e.target.checked } : x))
                        )
                      }
                    />
                    <Star size={11} className={m.exam_focus ? "fill-red-500 text-red-500" : ""} />
                    考点
                  </label>
                  <input
                    list="course-options"
                    placeholder="课程（可空）"
                    className="w-36 rounded-lg border border-slate-200 px-2 py-1 text-xs"
                    value={m.course}
                    onChange={(e) =>
                      setMaterials((prev) =>
                        prev.map((x, j) => (j === i ? { ...x, course: e.target.value } : x))
                      )
                    }
                  />
                  <input
                    placeholder="标签，逗号分隔"
                    className="w-56 rounded-lg border border-slate-200 px-2 py-1 text-xs"
                    aria-label={`资料 ${i + 1} 标签`}
                    value={tagInputs[i]}
                    onChange={(e) => {
                      const value = e.target.value;
                      setTagInputs((prev) => prev.map((text, j) => j === i ? value : text));
                      setMaterials((prev) => updateMaterialTags(prev, i, value));
                    }}
                  />
                  {needsReview(m.confidence) && (
                    <label className="flex items-center gap-1 text-xs text-amber-700">
                      <input type="checkbox" checked={m.reviewed} onChange={(e) => setMaterials((prev) => prev.map((row, j) => j === i ? { ...row, reviewed: e.target.checked } : row))} />
                      已核对标题、课程和标签
                    </label>
                  )}
                </div>
                {m.summary && <details className="mt-2 pl-8 text-xs text-slate-500"><summary className="cursor-pointer">查看资料摘要</summary><p className="mt-1 whitespace-pre-wrap break-words">{m.summary}</p></details>}
              </div>
            ))}
          </div>
        </div>
      )}

      {tasks.length === 0 && materials.length === 0 && (
        <div className="rounded-xl border border-dashed border-slate-200 py-8 text-center text-sm text-slate-400">
          没有识别出任务或资料。可以直接把内容粘贴进来试试，或手动添加。
        </div>
      )}

      <datalist id="course-options">
        {courseList.map((c) => (
          <option key={c} value={c} />
        ))}
      </datalist>

      {error && <div role="alert" className="rounded-xl bg-red-50 px-4 py-2.5 text-xs text-red-600">{error}</div>}

      {loadPendingWrite() && <Link to="/assistant" className="block text-sm text-brand-600 underline">前往对话助手确认上次提交结果</Link>}
      <div className="flex flex-wrap justify-end gap-3">
        <button disabled={applying || deadlineNotice || selectedCount === 0} onClick={() => {
          const prepared = prepareAgentApply(tasks,materials);
          if (prepared.error) { setError(prepared.error); return; }
          try { stage('ingest',{...prepared.payload,file_path:filePath,file_name:fileName,source,text:textSource}); setQueuedDone(true); } catch (cause) { setError((cause as Error).message); }
        }} className="rounded-xl border border-slate-200 px-4 py-2 text-sm text-slate-600 disabled:opacity-50">加入离线队列</button>
        <button
          onClick={apply}
          disabled={applying || deadlineNotice || selectedCount === 0 || !!loadPendingWrite()}
          className="flex items-center gap-2 rounded-xl bg-brand-600 px-5 py-2.5 text-sm font-medium text-white shadow-sm transition hover:bg-brand-700 disabled:opacity-50"
        >
          {applying ? <Spinner /> : <Check size={16} />}
          {applying ? "正在入库…" : `确认入库（${selectedCount} 项）`}
        </button>
      </div>
    </fieldset>
  );
}
