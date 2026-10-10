import { useEffect, useState } from "react";
import { Bell, Check, Pencil, Plus, TimerReset, Trash2 } from "lucide-react";
import { Link } from "react-router-dom";
import { apiCourses, apiTasks } from "../api";
import { useApp } from "../App";
import type { Course, Task } from "../types";
import { KIND_COLORS, TASK_KINDS } from "../types";
import { Empty, Modal, Spinner } from "../components/ui";
import { dueLabel, fmtDT } from "../utils";

const TABS = [
  { key: "open", label: "进行中" },
  { key: "done", label: "已完成" },
  { key: "overdue", label: "已逾期" },
] as const;

export default function Tasks() {
  const { refreshKey, bump } = useApp();
  const [courses, setCourses] = useState<Course[]>([]);
  const [tab, setTab] = useState<(typeof TABS)[number]["key"]>("open");
  const [items, setItems] = useState<Task[]>([]);
  const [courseId, setCourseId] = useState<number | "">("");
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<Task | null>(null);

  const load = async () => {
    try {
      if (tab === "overdue") {
        setItems(await apiTasks.overdue());
        return;
      }
      setItems(
        await apiTasks.list({
          done: tab === "done" ? true : false,
          course_id: courseId === "" ? undefined : courseId,
        })
      );
    } catch (e) {
      console.error(e);
    }
  };

  useEffect(() => {
    apiCourses.list().then(setCourses).catch(console.error);
  }, []);
  useEffect(() => {
    load();
  }, [tab, courseId, refreshKey]);

  const toggleDone = async (t: Task) => {
    await apiTasks.patch(t.id, { done: !t.done });
    bump();
  };
  const remove = async (t: Task) => {
    if (!confirm(`删除任务「${t.title}」？`)) return;
    await apiTasks.remove(t.id);
    bump();
  };

  const visible =
    tab === "open" ? items : tab === "done" ? items : items;

  return (
    <div className="mobile-page space-y-5">
      <div className="mobile-page-heading flex items-start justify-between">
        <div>
          <h1 className="text-xl font-bold text-slate-800">DDL 任务</h1>
          <p className="mt-1 text-sm text-slate-500">
            到期自动提醒（默认提前 3 天 / 1 天 / 2 小时）
          </p>
        </div>
        <button
          onClick={() => setAdding(true)}
          className="flex items-center gap-1.5 rounded-xl bg-brand-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-brand-700"
        >
          <Plus size={16} /> 手动添加
        </button>
      </div>

      <div className="mobile-filter-row flex flex-wrap items-center gap-2">
        <div className="flex rounded-xl bg-slate-100 p-1">
          {TABS.map((t) => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={`rounded-lg px-4 py-1.5 text-sm font-medium transition ${
                tab === t.key ? "bg-white text-slate-800 shadow-sm" : "text-slate-500"
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>
        <select
          className="ml-auto rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm"
          value={courseId}
          onChange={(e) => setCourseId(e.target.value === "" ? "" : Number(e.target.value))}
        >
          <option value="">全部课程</option>
          {courses.map((c) => (
            <option key={c.id} value={c.id}>{c.name}</option>
          ))}
        </select>
      </div>

      {visible.length === 0 ? (
        <Empty text={tab === "done" ? "还没有完成的任务" : tab === "overdue" ? "没有逾期任务 🎉" : "暂无任务，去「智能入库」添加"} />
      ) : (
        <div className="space-y-2">
          {visible.map((t) => {
            const due = dueLabel(t.due_at);
            return (
              <div
                key={t.id}
                className={`mobile-task-card group flex items-center gap-3 rounded-2xl border bg-white p-4 shadow-sm transition ${
                  t.done ? "border-slate-100 opacity-60" : "border-slate-200"
                }`}
              >
                <button
                  onClick={() => toggleDone(t)}
                  className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-full border-2 transition ${
                    t.done
                      ? "border-emerald-500 bg-emerald-500 text-white"
                      : "border-slate-300 hover:border-emerald-400"
                  }`}
                >
                  {t.done && <Check size={12} />}
                </button>
                <div className="mobile-task-content min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className={`rounded-md px-2 py-0.5 text-[10px] font-medium ${KIND_COLORS[t.kind]}`}>
                      {TASK_KINDS[t.kind]}
                    </span>
                    <span className={`truncate text-sm ${t.done ? "text-slate-400 line-through" : "text-slate-700"}`}>
                      {t.title}
                    </span>
                  </div>
                  <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] text-slate-400">
                    {t.course_name && <span>{t.course_name}</span>}
                    <span className="inline-flex items-center gap-1">
                      <Bell size={11} />
                      提前 {t.remind_offsets_h.map((h) => (h >= 24 ? `${h / 24}天` : `${h}小时`)).join(" / ")}
                    </span>
                    {t.source_text && (
                      <span className="truncate max-w-72">来源：「{t.source_text}」</span>
                    )}
                  </div>
                </div>
                <div className="mobile-task-due shrink-0 text-right">
                  <div className="text-sm font-medium text-slate-700">{fmtDT(t.due_at)}</div>
                  <div className={`text-xs font-medium ${due.cls}`}>{due.text}</div>
                </div>
                <div className="mobile-task-actions flex shrink-0 gap-1 opacity-0 transition group-hover:opacity-100">
                  {!t.done && <Link to={`/focus?task=${t.id}`} aria-label={`专注：${t.title}`} title="开始专注" className="mobile-task-focus rounded-lg p-1.5 text-brand-600 hover:bg-brand-50"><TimerReset size={16} /><span>专注</span></Link>}
                  <button aria-label={`编辑：${t.title}`} className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100" onClick={() => setEditing(t)}>
                    <Pencil size={14} />
                  </button>
                  <button aria-label={`删除：${t.title}`} className="rounded-lg p-1.5 text-slate-400 hover:bg-red-50 hover:text-red-500" onClick={() => remove(t)}>
                    <Trash2 size={14} />
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {(adding || editing) && (
        <Modal title={editing ? "编辑任务" : "手动添加任务"} onClose={() => { setAdding(false); setEditing(null); }}>
          <TaskForm
            task={editing}
            courses={courses}
            onDone={() => { setAdding(false); setEditing(null); bump(); }}
          />
        </Modal>
      )}
    </div>
  );
}

function TaskForm({ task, courses, onDone }: { task: Task | null; courses: Course[]; onDone: () => void }) {
  const [title, setTitle] = useState(task?.title ?? "");
  const [kind, setKind] = useState(task?.kind ?? "homework");
  const [courseId, setCourseId] = useState<number | "">(task?.course_id ?? "");
  const [dueLocal, setDueLocal] = useState(
    task ? toLocalInput(task.due_at) : toLocalInput(new Date(Date.now() + 7 * 864e5).toISOString())
  );
  const [saving, setSaving] = useState(false);

  const save = async () => {
    if (!title.trim() || !dueLocal) return;
    setSaving(true);
    try {
      // datetime-local 的输入是本地墙上时间 → toISOString() 转成带 Z 的 UTC，
      // 后端统一按 UTC 存储，任何时区的设备同步后显示的本地时刻都一致
      const due = new Date(dueLocal.length === 16 ? `${dueLocal}:00` : dueLocal).toISOString();
      if (task) {
        await apiTasks.patch(task.id, { title, kind, due_at: due, course_id: courseId === "" ? null : courseId });
      } else {
        await apiTasks.create({ title, kind, due_at: due, course_id: courseId === "" ? null : courseId });
      }
      onDone();
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-3">
      <label className="block text-xs text-slate-500">
        任务名
        <input className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2 text-sm" value={title} onChange={(e) => setTitle(e.target.value)} />
      </label>
      <div className="grid grid-cols-2 gap-3">
        <label className="block text-xs text-slate-500">
          类型
          <select className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2 text-sm" value={kind} onChange={(e) => setKind(e.target.value)}>
            {Object.entries(TASK_KINDS).map(([k, v]) => (
              <option key={k} value={k}>{v}</option>
            ))}
          </select>
        </label>
        <label className="block text-xs text-slate-500">
          课程
          <select className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2 text-sm" value={courseId} onChange={(e) => setCourseId(e.target.value === "" ? "" : Number(e.target.value))}>
            <option value="">无</option>
            {courses.map((c) => (
              <option key={c.id} value={c.id}>{c.name}</option>
            ))}
          </select>
        </label>
      </div>
      <label className="block text-xs text-slate-500">
        截止时间
        <input type="datetime-local" className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2 text-sm" value={dueLocal} onChange={(e) => setDueLocal(e.target.value)} />
      </label>
      <button onClick={save} disabled={saving || !title.trim()} className="w-full rounded-xl bg-brand-600 py-2.5 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-50">
        {saving ? <Spinner /> : "保存"}
      </button>
    </div>
  );
}

function toLocalInput(iso: string): string {
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
