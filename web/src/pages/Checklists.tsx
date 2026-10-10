import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import {
  Bookmark,
  Check,
  ChevronDown,
  ClipboardList,
  Copy,
  Download,
  ExternalLink,
  FileUp,
  LayoutTemplate,
  Link2,
  Pencil,
  Plus,
  Share2,
  Trash2,
  X,
} from "lucide-react";
import { apiChecklists, apiCourses, apiShares, apiTemplates } from "../api";
import { useApp } from "../App";
import type { Checklist, ChecklistItem, ChecklistTemplate, Course, ShareInfo } from "../types";
import DownloadLink from "../components/DownloadLink";
import { Empty, Modal, PageHeader, Spinner } from "../components/ui";
import { courseColor, dueLabel, fmtDate } from "../utils";

const EXPIRY_OPTIONS = [
  { label: "永久有效", value: null as number | null },
  { label: "1 天", value: 24 },
  { label: "7 天", value: 168 },
  { label: "30 天", value: 720 },
];

const STATUS_TABS = [
  { key: "all", label: "全部" },
  { key: "open", label: "进行中" },
  { key: "done", label: "已完成" },
] as const;

export default function Checklists() {
  const { refreshKey, bump } = useApp();
  const [tab, setTab] = useState<"mine" | "templates">("mine");
  const [items, setItems] = useState<Checklist[]>([]);
  const [templates, setTemplates] = useState<ChecklistTemplate[]>([]);
  const [categories, setCategories] = useState<string[]>([]);
  const [category, setCategory] = useState("");
  const [courses, setCourses] = useState<Course[]>([]);
  const [courseId, setCourseId] = useState<number | "">("");
  const [status, setStatus] = useState<(typeof STATUS_TABS)[number]["key"]>("all");
  const [expanded, setExpanded] = useState<number | null>(null);
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<Checklist | null>(null);
  const [sharing, setSharing] = useState<Checklist | null>(null);
  const [savingTpl, setSavingTpl] = useState<Checklist | null>(null);
  const [editingTpl, setEditingTpl] = useState<ChecklistTemplate | null>(null);
  const [importMsg, setImportMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const load = async () => {
    try {
      setItems(await apiChecklists.list({ course_id: courseId === "" ? undefined : courseId, status }));
    } catch (e) {
      console.error(e);
    }
  };

  useEffect(() => {
    apiCourses.list().then(setCourses).catch(console.error);
    apiTemplates.list().then(setTemplates).catch(console.error);
    apiTemplates.categories().then(setCategories).catch(console.error);
  }, [refreshKey]);
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [courseId, status, refreshKey]);

  // 分享页「导入到我的 StudyOS」跳转过来：?import=<分享链接>
  useEffect(() => {
    const url = new URLSearchParams(window.location.search).get("import");
    if (!url) return;
    setImportMsg("正在导入分享清单…");
    apiChecklists
      .importFromUrl(url)
      .then((c) => {
        setImportMsg(`已导入「${c.title}」，勾选进度已清零重新开始`);
        window.history.replaceState(null, "", window.location.pathname);
        setTab("mine");
        bump();
      })
      .catch((e) => setImportMsg(`导入失败：${e instanceof Error ? e.message : String(e)}`))
      .finally(() => setTimeout(() => setImportMsg(""), 7000));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const toggleItem = async (cl: Checklist, idx: number) => {
    const next = cl.items.map((it, i) => (i === idx ? { ...it, done: !it.done } : it));
    try {
      const updated = await apiChecklists.patch(cl.id, { items: next });
      setItems((xs) => xs.map((x) => (x.id === cl.id ? updated : x)));
      bump();
    } catch (e) {
      console.error(e);
    }
  };

  const changeItems = async (cl: Checklist, next: ChecklistItem[]) => {
    try {
      const updated = await apiChecklists.patch(cl.id, { items: next });
      setItems((xs) => xs.map((x) => (x.id === cl.id ? updated : x)));
      bump();
    } catch (e) {
      console.error(e);
    }
  };

  const remove = async (cl: Checklist) => {
    if (!confirm(`删除清单「${cl.title}」？其分享链接将同时失效。`)) return;
    await apiChecklists.remove(cl.id);
    bump();
  };

  const useTemplate = async (tpl: ChecklistTemplate) => {
    setBusy(true);
    try {
      await apiChecklists.create({
        title: tpl.title,
        description: tpl.description,
        tags: tpl.tags,
        items: tpl.items.map((i) => ({ text: i.text, note: i.note })),
      });
      setTab("mine");
      bump();
    } catch (e) {
      console.error(e);
    } finally {
      setBusy(false);
    }
  };

  const removeTemplate = async (tpl: ChecklistTemplate) => {
    if (!confirm(`删除模版「${tpl.title}」？`)) return;
    await apiTemplates.remove(tpl.id);
    bump();
  };

  const onImportFile = async (f: File) => {
    try {
      const payload = JSON.parse(await f.text());
      const c = await apiChecklists.importPayload(payload);
      setImportMsg(`已导入「${c.title}」`);
      setTab("mine");
      bump();
    } catch (e) {
      setImportMsg(`导入失败：${e instanceof Error ? e.message : "JSON 格式不正确"}`);
    } finally {
      setTimeout(() => setImportMsg(""), 7000);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const visibleTemplates = category
    ? templates.filter((t) => t.category === category)
    : templates;

  return (
    <div className="space-y-5">
      <PageHeader
        title="清单 · 模版库"
        desc="勾选清单 + 内置模版一键套用 + 分享链接给同学只读查看"
        actions={
          <>
            <button
              onClick={() => fileRef.current?.click()}
              className="flex items-center gap-1.5 rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm font-medium text-slate-600 hover:bg-slate-50"
              title="导入分享清单 JSON（分享页「下载 JSON」得到的文件）"
            >
              <FileUp size={16} /> 导入 JSON
            </button>
            <input
              ref={fileRef}
              type="file"
              accept=".json,application/json"
              className="hidden"
              onChange={(e) => e.target.files?.[0] && onImportFile(e.target.files[0])}
            />
            <button
              onClick={() => setCreating(true)}
              className="flex items-center gap-1.5 rounded-xl bg-brand-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-brand-700"
            >
              <Plus size={16} /> 新建清单
            </button>
          </>
        }
      />

      {importMsg && (
        <div className="rounded-xl border border-brand-200 bg-brand-50 px-4 py-3 text-sm text-brand-700">
          {importMsg}
        </div>
      )}

      <div className="flex rounded-xl bg-slate-100 p-1">
        {[
          { key: "mine", label: `我的清单（${items.length}）` },
          { key: "templates", label: `模版库（${templates.length}）` },
        ].map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key as "mine" | "templates")}
            className={`rounded-lg px-4 py-1.5 text-sm font-medium transition ${
              tab === t.key ? "bg-white text-slate-800 shadow-sm" : "text-slate-500"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === "mine" ? (
        <MineView
          items={items}
          courses={courses}
          courseId={courseId}
          setCourseId={setCourseId}
          status={status}
          setStatus={setStatus}
          expanded={expanded}
          setExpanded={setExpanded}
          onToggle={toggleItem}
          onChangeItems={changeItems}
          onShare={setSharing}
          onSaveTpl={setSavingTpl}
          onEdit={setEditing}
          onRemove={remove}
          onNew={() => setCreating(true)}
        />
      ) : (
        <TemplatesView
          templates={visibleTemplates}
          categories={categories}
          category={category}
          setCategory={setCategory}
          busy={busy}
          onUse={useTemplate}
          onEdit={setEditingTpl}
          onRemove={removeTemplate}
        />
      )}

      {(creating || editing) && (
        <Modal title={editing ? "编辑清单" : "新建清单"} onClose={() => { setCreating(false); setEditing(null); }} wide>
          <ChecklistForm
            checklist={editing}
            courses={courses}
            onDone={() => { setCreating(false); setEditing(null); bump(); }}
          />
        </Modal>
      )}

      {sharing && (
        <Modal title={`分享清单：${sharing.title}`} onClose={() => setSharing(null)}>
          <SharePanel checklist={sharing} />
        </Modal>
      )}

      {savingTpl && (
        <Modal title="存为模版" onClose={() => setSavingTpl(null)}>
          <TemplateForm
            title={savingTpl.title}
            description={savingTpl.description}
            items={savingTpl.items}
            categories={categories}
            onSave={async (b) => {
              await apiChecklists.saveAsTemplate(savingTpl.id, b);
              setSavingTpl(null);
              bump();
            }}
          />
        </Modal>
      )}

      {editingTpl && (
        <Modal title="编辑模版" onClose={() => setEditingTpl(null)} wide>
          <TemplateForm
            tpl={editingTpl}
            categories={categories}
            onSave={async (b) => {
              await apiTemplates.patch(editingTpl.id, b);
              setEditingTpl(null);
              bump();
            }}
          />
        </Modal>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 我的清单
// ---------------------------------------------------------------------------

function MineView({
  items, courses, courseId, setCourseId, status, setStatus, expanded, setExpanded,
  onToggle, onChangeItems, onShare, onSaveTpl, onEdit, onRemove, onNew,
}: {
  items: Checklist[];
  courses: Course[];
  courseId: number | "";
  setCourseId: (v: number | "") => void;
  status: string;
  setStatus: (v: "all" | "open" | "done") => void;
  expanded: number | null;
  setExpanded: (id: number | null) => void;
  onToggle: (cl: Checklist, idx: number) => void;
  onChangeItems: (cl: Checklist, next: ChecklistItem[]) => void;
  onShare: (cl: Checklist) => void;
  onSaveTpl: (cl: Checklist) => void;
  onEdit: (cl: Checklist) => void;
  onRemove: (cl: Checklist) => void;
  onNew: () => void;
}) {
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex rounded-xl bg-slate-100 p-1">
          {STATUS_TABS.map((t) => (
            <button
              key={t.key}
              onClick={() => setStatus(t.key)}
              className={`rounded-lg px-4 py-1.5 text-sm font-medium transition ${
                status === t.key ? "bg-white text-slate-800 shadow-sm" : "text-slate-500"
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

      {items.length === 0 ? (
        <Empty text="还没有清单。新建一个，或到「模版库」一键套用现成的清单。" />
      ) : (
        <div className="space-y-3">
          {items.map((cl) => {
            const pct = cl.total_count ? Math.round((cl.done_count / cl.total_count) * 100) : 0;
            const isOpen = expanded === cl.id;
            return (
              <div key={cl.id} className="rounded-2xl border border-slate-200 bg-white shadow-sm">
                {/* 头部：点击展开 */}
                <div className="flex cursor-pointer items-center gap-3 p-4" onClick={() => setExpanded(isOpen ? null : cl.id)}>
                  <div className={`relative h-10 w-10 shrink-0 rounded-full ${pct === 100 ? "bg-emerald-500" : "bg-white"}`}>
                    <svg viewBox="0 0 36 36" className="h-10 w-10 -rotate-90">
                      <circle cx="18" cy="18" r="15" fill="none" strokeWidth="4" className={pct === 100 ? "stroke-white/30" : "stroke-slate-200"} />
                      <circle
                        cx="18" cy="18" r="15" fill="none" strokeWidth="4"
                        strokeLinecap="round" strokeDasharray={`${(pct / 100) * 94.2} 94.2`}
                        className={pct === 100 ? "stroke-white" : "stroke-brand-500"}
                      />
                    </svg>
                    <span className={`absolute inset-0 flex items-center justify-center text-[10px] font-bold ${pct === 100 ? "text-white" : "text-slate-500"}`}>
                      {pct}%
                    </span>
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className={`truncate text-sm font-medium ${pct === 100 && cl.total_count > 0 ? "text-slate-400 line-through" : "text-slate-800"}`}>
                        {cl.title}
                      </span>
                      {cl.course_name && (
                        <span
                          className="shrink-0 rounded-md px-2 py-0.5 text-[10px] font-medium text-white"
                          style={{ backgroundColor: courseColor(cl.course_name) }}
                        >
                          {cl.course_name}
                        </span>
                      )}
                    </div>
                    <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] text-slate-400">
                      <span>{cl.done_count}/{cl.total_count} 项</span>
                      {cl.due_at && (
                        <span className={dueLabel(cl.due_at).cls}>
                          截止 {fmtDate(cl.due_at)}（{dueLabel(cl.due_at).text}）
                        </span>
                      )}
                      {cl.tags.slice(0, 4).map((t) => (
                        <span key={t} className="rounded bg-slate-100 px-1.5 py-0.5 text-slate-500">#{t}</span>
                      ))}
                    </div>
                  </div>
                  <div className="flex shrink-0 items-center gap-1" onClick={(e) => e.stopPropagation()}>
                    <IconBtn title="分享链接" onClick={() => onShare(cl)}><Link2 size={15} /></IconBtn>
                    <IconBtn title="存为模版" onClick={() => onSaveTpl(cl)}><Bookmark size={15} /></IconBtn>
                    <DownloadLink
                      path={apiChecklists.mdUrl(cl.id)}
                      fallbackName={`studyos-checklist-${cl.id}.md`}
                      title="导出 Markdown"
                      className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-600"
                    >
                      <Download size={15} />
                    </DownloadLink>
                    <IconBtn title="编辑" onClick={() => onEdit(cl)}><Pencil size={15} /></IconBtn>
                    <IconBtn title="删除" danger onClick={() => onRemove(cl)}><Trash2 size={15} /></IconBtn>
                    <ChevronDown size={16} className={`ml-1 text-slate-300 transition-transform ${isOpen ? "rotate-180" : ""}`} />
                  </div>
                </div>

                {/* 展开：清单项 */}
                {isOpen && (
                  <div className="border-t border-slate-100 px-4 py-3">
                    {cl.description && <p className="mb-3 whitespace-pre-wrap text-sm text-slate-500">{cl.description}</p>}
                    <div className="space-y-1">
                      {cl.items.map((it, idx) => (
                        <div key={idx} className="group flex items-center gap-3 rounded-xl px-2 py-1.5 hover:bg-slate-50">
                          <button
                            onClick={() => onToggle(cl, idx)}
                            className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-md border-2 transition ${
                              it.done ? "border-emerald-500 bg-emerald-500 text-white" : "border-slate-300 hover:border-emerald-400"
                            }`}
                          >
                            {it.done && <Check size={12} />}
                          </button>
                          <span className={`min-w-0 flex-1 text-sm ${it.done ? "text-slate-400 line-through" : "text-slate-700"}`}>
                            {it.text}
                          </span>
                          {it.note && <span className="shrink-0 rounded bg-amber-50 px-1.5 py-0.5 text-[11px] text-amber-600">{it.note}</span>}
                          <button
                            className="shrink-0 rounded-lg p-1 text-slate-300 opacity-0 transition hover:bg-red-50 hover:text-red-500 group-hover:opacity-100"
                            onClick={() => onChangeItems(cl, cl.items.filter((_, i) => i !== idx))}
                          >
                            <X size={13} />
                          </button>
                        </div>
                      ))}
                      {cl.items.length === 0 && <p className="py-2 text-center text-xs text-slate-400">空清单，在下方添加第一项</p>}
                    </div>
                    <AddItemRow onAdd={(text, note) => onChangeItems(cl, [...cl.items, { text, done: false, note }])} />
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function IconBtn({ children, title, danger, onClick }: { children: ReactNode; title: string; danger?: boolean; onClick: () => void }) {
  return (
    <button
      title={title}
      onClick={onClick}
      className={`rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-600 ${danger ? "hover:bg-red-50 hover:text-red-500" : ""}`}
    >
      {children}
    </button>
  );
}

/** 行内添加清单项：文本 + 可选备注，回车即添加 */
export function AddItemRow({ onAdd, placeholder }: { onAdd: (text: string, note: string) => void; placeholder?: string }) {
  const [text, setText] = useState("");
  const [note, setNote] = useState("");
  const submit = () => {
    if (!text.trim()) return;
    onAdd(text.trim(), note.trim());
    setText("");
    setNote("");
  };
  return (
    <div className="mt-2 flex items-center gap-2 pl-2">
      <Plus size={14} className="shrink-0 text-slate-300" />
      <input
        className="min-w-0 flex-1 rounded-lg border border-slate-200 px-2.5 py-1.5 text-sm outline-none focus:border-brand-400"
        placeholder={placeholder ?? "添加清单项，回车确认"}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && submit()}
      />
      <input
        className="w-28 shrink-0 rounded-lg border border-slate-200 px-2.5 py-1.5 text-xs outline-none focus:border-brand-400"
        placeholder="备注（可选）"
        value={note}
        onChange={(e) => setNote(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && submit()}
      />
      <button
        onClick={submit}
        disabled={!text.trim()}
        className="shrink-0 rounded-lg bg-brand-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-brand-700 disabled:opacity-40"
      >
        添加
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 模版库
// ---------------------------------------------------------------------------

function TemplatesView({
  templates, categories, category, setCategory, busy, onUse, onEdit, onRemove,
}: {
  templates: ChecklistTemplate[];
  categories: string[];
  category: string;
  setCategory: (c: string) => void;
  busy: boolean;
  onUse: (t: ChecklistTemplate) => void;
  onEdit: (t: ChecklistTemplate) => void;
  onRemove: (t: ChecklistTemplate) => void;
}) {
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-1.5">
        <button
          onClick={() => setCategory("")}
          className={`rounded-full px-3.5 py-1.5 text-xs font-medium transition ${
            category === "" ? "bg-brand-600 text-white" : "bg-slate-100 text-slate-500 hover:bg-slate-200"
          }`}
        >
          全部
        </button>
        {categories.map((c) => (
          <button
            key={c}
            onClick={() => setCategory(c)}
            className={`rounded-full px-3.5 py-1.5 text-xs font-medium transition ${
              category === c ? "bg-brand-600 text-white" : "bg-slate-100 text-slate-500 hover:bg-slate-200"
            }`}
          >
            {c}
          </button>
        ))}
      </div>

      {templates.length === 0 ? (
        <Empty text="这个分类下还没有模版。" />
      ) : (
        <div className="grid gap-3 md:grid-cols-2">
          {templates.map((t) => (
            <div key={t.id} className="flex flex-col rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
              <div className="flex items-start gap-2">
                <div className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-xl ${t.builtin ? "bg-brand-50 text-brand-600" : "bg-emerald-50 text-emerald-600"}`}>
                  <LayoutTemplate size={17} />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="truncate text-sm font-semibold text-slate-800">{t.title}</span>
                    <span className={`shrink-0 rounded-md px-1.5 py-0.5 text-[10px] font-medium ${t.builtin ? "bg-brand-100 text-brand-700" : "bg-emerald-100 text-emerald-700"}`}>
                      {t.builtin ? "内置" : "我的模版"}
                    </span>
                  </div>
                  <div className="mt-0.5 flex items-center gap-2 text-[11px] text-slate-400">
                    <span>{t.category}</span>
                    <span>·</span>
                    <span>{t.items.length} 项</span>
                  </div>
                </div>
              </div>
              {t.description && <p className="mt-2 line-clamp-2 text-xs text-slate-500">{t.description}</p>}
              <ul className="mt-2 space-y-0.5 text-xs text-slate-500">
                {t.items.slice(0, 3).map((it, i) => (
                  <li key={i} className="flex gap-1.5">
                    <span className="text-slate-300">•</span>
                    <span className="min-w-0 truncate">{it.text}</span>
                  </li>
                ))}
                {t.items.length > 3 && <li className="pl-3.5 text-[11px] text-slate-400">… 还有 {t.items.length - 3} 项</li>}
              </ul>
              <div className="mt-3 flex items-center gap-2 border-t border-slate-100 pt-3">
                <button
                  onClick={() => onUse(t)}
                  disabled={busy}
                  className="flex items-center gap-1.5 rounded-lg bg-brand-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-brand-700 disabled:opacity-50"
                >
                  {busy ? <Spinner /> : <ClipboardList size={13} />}
                  使用此模版
                </button>
                <span className="text-[11px] text-slate-400">复制生成新清单，不改动模版</span>
                {!t.builtin && (
                  <div className="ml-auto flex gap-1">
                    <IconBtn title="编辑模版" onClick={() => onEdit(t)}><Pencil size={14} /></IconBtn>
                    <IconBtn title="删除模版" danger onClick={() => onRemove(t)}><Trash2 size={14} /></IconBtn>
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 表单：新建 / 编辑清单
// ---------------------------------------------------------------------------

function ChecklistForm({ checklist, courses, onDone }: { checklist: Checklist | null; courses: Course[]; onDone: () => void }) {
  const [title, setTitle] = useState(checklist?.title ?? "");
  const [description, setDescription] = useState(checklist?.description ?? "");
  const [courseId, setCourseId] = useState<number | "">(checklist?.course_id ?? "");
  const [tags, setTags] = useState(checklist?.tags.join(", ") ?? "");
  const [dueLocal, setDueLocal] = useState(checklist?.due_at ? toLocalInput(checklist.due_at) : "");
  const [items, setItems] = useState<ChecklistItem[]>(checklist?.items.map((i) => ({ ...i })) ?? []);
  const [saving, setSaving] = useState(false);

  const save = async () => {
    if (!title.trim()) return;
    setSaving(true);
    try {
      const body = {
        title: title.trim(),
        description: description.trim(),
        course_id: courseId === "" ? null : courseId,
        tags: tags.split(/[,，]/).map((t) => t.trim()).filter(Boolean),
        due_at: dueLocal ? new Date(dueLocal.length === 16 ? `${dueLocal}:00` : dueLocal).toISOString() : null,
        items,
      };
      if (checklist) await apiChecklists.patch(checklist.id, body);
      else await apiChecklists.create(body);
      onDone();
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-3">
      <label className="block text-xs text-slate-500">
        清单标题
        <input className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2 text-sm" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="如：期末复习清单" />
      </label>
      <label className="block text-xs text-slate-500">
        描述（可选）
        <textarea className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2 text-sm" rows={2} value={description} onChange={(e) => setDescription(e.target.value)} placeholder="这张清单用来做什么" />
      </label>
      <div className="grid grid-cols-2 gap-3">
        <label className="block text-xs text-slate-500">
          课程
          <select className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2 text-sm" value={courseId} onChange={(e) => setCourseId(e.target.value === "" ? "" : Number(e.target.value))}>
            <option value="">无</option>
            {courses.map((c) => (
              <option key={c.id} value={c.id}>{c.name}</option>
            ))}
          </select>
        </label>
        <label className="block text-xs text-slate-500">
          截止时间（可选）
          <input type="datetime-local" className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2 text-sm" value={dueLocal} onChange={(e) => setDueLocal(e.target.value)} />
        </label>
      </div>
      <label className="block text-xs text-slate-500">
        标签（逗号分隔，可选）
        <input className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2 text-sm" value={tags} onChange={(e) => setTags(e.target.value)} placeholder="期末, 冲刺" />
      </label>

      <div className="rounded-xl border border-slate-100 bg-slate-50/60 p-3">
        <div className="mb-1 text-xs font-medium text-slate-500">清单项（{items.length}）</div>
        <div className="max-h-56 space-y-1.5 overflow-y-auto">
          {items.map((it, idx) => (
            <div key={idx} className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => setItems((xs) => xs.map((x, i) => (i === idx ? { ...x, done: !x.done } : x)))}
                className={`flex h-4.5 w-4.5 shrink-0 items-center justify-center rounded border-2 transition ${
                  it.done ? "border-emerald-500 bg-emerald-500 text-white" : "border-slate-300"
                }`}
                title="勾选状态"
              >
                {it.done && <Check size={11} />}
              </button>
              <input
                className="min-w-0 flex-1 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-sm"
                value={it.text}
                onChange={(e) => setItems((xs) => xs.map((x, i) => (i === idx ? { ...x, text: e.target.value } : x)))}
                placeholder="清单项内容"
              />
              <input
                className="w-24 shrink-0 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-xs"
                value={it.note}
                onChange={(e) => setItems((xs) => xs.map((x, i) => (i === idx ? { ...x, note: e.target.value } : x)))}
                placeholder="备注"
              />
              <button
                type="button"
                onClick={() => setItems((xs) => xs.filter((_, i) => i !== idx))}
                className="shrink-0 rounded-lg p-1 text-slate-300 hover:bg-red-50 hover:text-red-500"
              >
                <X size={14} />
              </button>
            </div>
          ))}
        </div>
        <AddItemRow onAdd={(text, note) => setItems((xs) => [...xs, { text, done: false, note }])} />
      </div>

      <button onClick={save} disabled={saving || !title.trim()} className="w-full rounded-xl bg-brand-600 py-2.5 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-50">
        {saving ? <Spinner /> : "保存"}
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 表单：存为模版 / 编辑模版
// ---------------------------------------------------------------------------

function TemplateForm({
  tpl, title, description, items, categories, onSave,
}: {
  tpl?: ChecklistTemplate;
  title?: string;
  description?: string;
  items?: ChecklistItem[];
  categories: string[];
  onSave: (b: { title: string; category: string; description: string; items: { text: string; note: string; done: boolean }[] }) => Promise<void>;
}) {
  const [t, setT] = useState(tpl?.title ?? title ?? "");
  const [d, setD] = useState(tpl?.description ?? description ?? "");
  const [cat, setCat] = useState(tpl?.category ?? categories[0] ?? "日常学习");
  const [list, setList] = useState<ChecklistItem[]>(
    (tpl?.items ?? items ?? []).map((i) => ({ text: i.text, note: i.note, done: false })),
  );
  const [saving, setSaving] = useState(false);

  const save = async () => {
    if (!t.trim()) return;
    setSaving(true);
    try {
      await onSave({
        title: t.trim(),
        category: cat,
        description: d.trim(),
        items: list.filter((i) => i.text.trim()).map((i) => ({ text: i.text.trim(), note: i.note.trim(), done: false })),
      });
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-3">
      <label className="block text-xs text-slate-500">
        模版标题
        <input className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2 text-sm" value={t} onChange={(e) => setT(e.target.value)} />
      </label>
      <div className="grid grid-cols-2 gap-3">
        <label className="block text-xs text-slate-500">
          分类
          <select className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2 text-sm" value={cat} onChange={(e) => setCat(e.target.value)}>
            {categories.map((c) => (
              <option key={c} value={c}>{c}</option>
            ))}
          </select>
        </label>
        <label className="block text-xs text-slate-500">
          描述（可选）
          <input className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2 text-sm" value={d} onChange={(e) => setD(e.target.value)} />
        </label>
      </div>
      <div className="rounded-xl border border-slate-100 bg-slate-50/60 p-3">
        <div className="mb-1 text-xs font-medium text-slate-500">模版项（保存后勾选状态清零）</div>
        <div className="max-h-56 space-y-1.5 overflow-y-auto">
          {list.map((it, idx) => (
            <div key={idx} className="flex items-center gap-2">
              <span className="text-slate-300">•</span>
              <input
                className="min-w-0 flex-1 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-sm"
                value={it.text}
                onChange={(e) => setList((xs) => xs.map((x, i) => (i === idx ? { ...x, text: e.target.value } : x)))}
              />
              <input
                className="w-24 shrink-0 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-xs"
                value={it.note}
                onChange={(e) => setList((xs) => xs.map((x, i) => (i === idx ? { ...x, note: e.target.value } : x)))}
                placeholder="备注"
              />
              <button
                type="button"
                onClick={() => setList((xs) => xs.filter((_, i) => i !== idx))}
                className="shrink-0 rounded-lg p-1 text-slate-300 hover:bg-red-50 hover:text-red-500"
              >
                <X size={14} />
              </button>
            </div>
          ))}
        </div>
        <AddItemRow onAdd={(text, note) => setList((xs) => [...xs, { text, done: false, note }])} />
      </div>
      <button onClick={save} disabled={saving || !t.trim()} className="w-full rounded-xl bg-brand-600 py-2.5 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-50">
        {saving ? <Spinner /> : "保存模版"}
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 分享面板
// ---------------------------------------------------------------------------

function SharePanel({ checklist }: { checklist: Checklist }) {
  const [shares, setShares] = useState<ShareInfo[]>([]);
  const [expiresH, setExpiresH] = useState<number | null>(null);
  const [creating, setCreating] = useState(false);
  const [created, setCreated] = useState<ShareInfo | null>(null);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState("");

  const load = () => apiShares.forChecklist(checklist.id).then(setShares).catch((e) => setError(String(e)));
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [checklist.id]);

  const create = async () => {
    setCreating(true);
    setError("");
    try {
      const s = await apiShares.create(checklist.id, expiresH);
      setCreated(s);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setCreating(false);
    }
  };

  const revoke = async (token: string) => {
    try {
      await apiShares.revoke(token);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const copy = async (url: string) => {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      window.prompt("复制分享链接：", url);
    }
  };

  return (
    <div className="space-y-4">
      <div className="rounded-xl bg-slate-50 p-3 text-xs leading-5 text-slate-500">
        <div className="flex items-center gap-1.5 font-medium text-slate-600">
          <Share2 size={13} /> 分享规则
        </div>
        同学打开链接即可<strong>只读查看</strong>清单；他们勾选的进度只保存在自己的浏览器里，不会改动你的清单。
        到期自动失效，也可随时撤销。
      </div>

      <div className="flex items-center gap-2">
        <select
          className="flex-1 rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm"
          value={expiresH === null ? "null" : String(expiresH)}
          onChange={(e) => setExpiresH(e.target.value === "null" ? null : Number(e.target.value))}
        >
          {EXPIRY_OPTIONS.map((o) => (
            <option key={o.label} value={o.value === null ? "null" : String(o.value)}>{o.label}</option>
          ))}
        </select>
        <button
          onClick={create}
          disabled={creating}
          className="flex shrink-0 items-center gap-1.5 rounded-xl bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-50"
        >
          {creating ? <Spinner /> : <Link2 size={15} />} 生成分享链接
        </button>
      </div>

      {error && <p className="text-xs text-red-500">{error}</p>}

      {created && (
        <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-3">
          <div className="flex items-center justify-between gap-2">
            <a
              href={created.url}
              target="_blank"
              rel="noreferrer"
              className="flex min-w-0 items-center gap-1.5 truncate text-xs font-medium text-emerald-700 hover:underline"
            >
              <ExternalLink size={13} className="shrink-0" />
              <span className="truncate">{created.url}</span>
            </a>
            <button
              onClick={() => copy(created.url)}
              className="flex shrink-0 items-center gap-1 rounded-lg bg-emerald-600 px-2.5 py-1.5 text-xs font-medium text-white hover:bg-emerald-700"
            >
              {copied ? <Check size={13} /> : <Copy size={13} />}
              {copied ? "已复制" : "复制链接"}
            </button>
          </div>
          <p className="mt-1 text-[11px] text-emerald-600">
            {created.expires_at ? `有效期至 ${fmtDate(created.expires_at)}` : "永久有效"} · 点击链接可预览分享页
          </p>
        </div>
      )}

      {shares.length > 0 && (
        <div>
          <div className="mb-1.5 text-xs font-medium text-slate-500">已有分享链接（{shares.length}）</div>
          <div className="max-h-44 space-y-1.5 overflow-y-auto">
            {shares.map((s) => (
              <div key={s.token} className={`flex items-center gap-2 rounded-xl border px-3 py-2 text-xs ${s.revoked ? "border-slate-100 bg-slate-50 opacity-60" : "border-slate-200 bg-white"}`}>
                <div className="min-w-0 flex-1">
                  <div className="truncate font-mono text-[11px] text-slate-500">{s.token.slice(0, 16)}…</div>
                  <div className="text-[11px] text-slate-400">
                    {s.revoked ? "已撤销" : s.expires_at ? `${fmtDate(s.expires_at)} 到期` : "永久有效"}
                    {" · "}{s.view_count} 次查看
                  </div>
                </div>
                {!s.revoked && (
                  <>
                    <button onClick={() => copy(s.url)} title="复制链接" className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100">
                      <Copy size={13} />
                    </button>
                    <button onClick={() => revoke(s.token)} className="rounded-lg px-2 py-1 text-[11px] font-medium text-red-500 hover:bg-red-50">
                      撤销
                    </button>
                  </>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function toLocalInput(iso: string): string {
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
