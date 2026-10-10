import { lazy, Suspense, useEffect, useRef, useState } from "react";
import {
  BrainCircuit, Check, CheckCircle2, Eye, FileText, FolderInput, ListChecks, Pencil,
  Search, SlidersHorizontal, Star, Tags, Trash2, Wand2,
} from "lucide-react";
import { apiCourses, apiMaterials, apiSearch } from "../api";
import { useApp } from "../App";
import RefinePanel from "../components/RefinePanel";
import type { ChapterFacet, Course, Material, MaterialBatchResult, SemanticStatus, TagFacet } from "../types";
import { DIFFICULTIES, DIFF_COLORS, MATERIAL_TYPES } from "../types";
import { Empty, Modal, PageHeader, Spinner } from "../components/ui";
import { courseColor, fmtDT } from "../utils";

// 原文件预览（PDF iframe / PPTX pptx-preview）：独立 chunk，仅在打开预览时加载，
// 避免把 pptx-preview + echarts（~600KB gzip）拖进主包
const MaterialPreview = lazy(() => import("../components/MaterialPreview"));

type SearchMode = "fts" | "semantic" | "hybrid";

const MODE_LABELS: Record<SearchMode, string> = {
  fts: "全文",
  semantic: "语义",
  hybrid: "混合",
};

const PAGE_STEP = 50;
/** 批量操作单次上限（与后端 MAX_MATERIAL_BATCH 一致）：全选全部时最多取前 500 条 */
const BATCH_LIMIT = 500;

/** 渲染带【】高亮的摘录 */
function Highlight({ text }: { text: string }) {
  if (!text) return null;
  const parts = text.split(/(【[^】]*】)/g);
  return (
    <p className="mt-1.5 line-clamp-2 text-xs text-slate-500">
      {parts.map((p, i) =>
        p.startsWith("【") ? (
          <mark key={i} className="rounded-sm bg-amber-200 px-0.5">
            {p.slice(1, -1)}
          </mark>
        ) : (
          <span key={i}>{p}</span>
        )
      )}
    </p>
  );
}

export default function Materials() {
  const { refreshKey, bump } = useApp();
  const [courses, setCourses] = useState<Course[]>([]);
  const [items, setItems] = useState<Material[]>([]);
  const [snips, setSnips] = useState<string[]>([]);
  const [scores, setScores] = useState<(number | null)[]>([]);
  const [total, setTotal] = useState(0);
  const [truncated, setTruncated] = useState(false);
  const [limit, setLimit] = useState(PAGE_STEP);
  const [q, setQ] = useState("");
  const [mode, setMode] = useState<SearchMode>("hybrid");
  const [courseId, setCourseId] = useState<number | "">("");
  const [type, setType] = useState("");
  // 归档过滤：章节（独立字段）· 难度 · 考试重点
  const [chapter, setChapter] = useState("");
  const [difficulty, setDifficulty] = useState("");
  const [examFocus, setExamFocus] = useState(false);
  const [chapterFacets, setChapterFacets] = useState<ChapterFacet[]>([]);
  const [loading, setLoading] = useState(false);
  const [editing, setEditing] = useState<Material | null>(null);
  // 原文件内嵌预览（PDF/PPTX/图片，来自上传的附件）
  const [previewing, setPreviewing] = useState<Material | null>(null);
  // 提炼笔记弹窗（AI 提炼重点/考点）
  const [refining, setRefining] = useState<Material | null>(null);
  // 多标签过滤：任一（any）/ 全部（all），过滤在后端 SQL 内完成
  const [tagFilter, setTagFilter] = useState<string[]>([]);
  const [tagMode, setTagMode] = useState<"any" | "all">("any");
  const [facets, setFacets] = useState<TagFacet[]>([]);
  const [semStatus, setSemStatus] = useState<SemanticStatus | null>(null);
  // 批量操作：选择模式 + 已勾选资料 id（跨页保持）+ 当前批量弹窗
  const [selectMode, setSelectMode] = useState(false);
  const [selected, setSelected] = useState<number[]>([]);
  const [selectingAll, setSelectingAll] = useState(false);
  const [batchOp, setBatchOp] = useState<"tags" | "course" | "fields" | null>(null);
  // 删除确认弹窗（单条 / 批量共用，替代原生 confirm）
  const [confirmDel, setConfirmDel] = useState<{ ids: number[]; label: string } | null>(null);
  // 操作结果轻提示
  const [toast, setToast] = useState<string | null>(null);
  const toastTimer = useRef<number | null>(null);

  const showToast = (msg: string) => {
    setToast(msg);
    if (toastTimer.current) window.clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => setToast(null), 2600);
  };
  useEffect(() => () => {
    if (toastTimer.current) window.clearTimeout(toastTimer.current);
  }, []);

  const load = async (keyword: string, m: SearchMode, take: number) => {
    setLoading(true);
    try {
      const r = await apiSearch.query(keyword, {
        course_id: courseId === "" ? undefined : courseId,
        type: type || undefined,
        chapter: chapter || undefined,
        difficulty: difficulty || undefined,
        exam_focus: examFocus || undefined,
        tags: tagFilter.length ? tagFilter : undefined,
        tag_mode: tagMode,
        mode: m,
        limit: take,
      });
      setItems(r.materials);
      setSnips(r.snippet);
      setScores(r.scores ?? []);
      setTotal(r.total);
      setTruncated(r.truncated || r.total > r.materials.length);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    apiCourses.list().then(setCourses).catch(console.error);
    apiSearch.semanticStatus().then(setSemStatus).catch(() => setSemStatus(null));
  }, []);

  useEffect(() => {
    const t = setTimeout(() => load(q, mode, limit), 250);
    return () => clearTimeout(t);
  }, [q, mode, courseId, type, chapter, difficulty, examFocus, tagFilter, tagMode, limit, refreshKey]);

  // 标签云：来自后端全库聚合（SQL GROUP BY），不再只统计当前一页结果
  useEffect(() => {
    apiMaterials
      .tags({ course_id: courseId === "" ? undefined : courseId, type: type || undefined })
      .then(setFacets)
      .catch(console.error);
  }, [courseId, type, refreshKey]);

  // 章节下拉：来自后端全库聚合（按章节号自然排序），受课程/类型过滤约束
  useEffect(() => {
    apiMaterials
      .chapters({ course_id: courseId === "" ? undefined : courseId, type: type || undefined })
      .then(setChapterFacets)
      .catch(console.error);
  }, [courseId, type, refreshKey]);

  const toggleTag = (t: string) => {
    setLimit(PAGE_STEP);
    setTagFilter((prev) => (prev.includes(t) ? prev.filter((x) => x !== t) : [...prev, t]));
  };

  // ---- 批量操作（批量删除 / 改标签 / 换课程 / 归档设置）----
  const toggleSelect = (id: number) =>
    setSelected((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));

  const exitSelectMode = () => {
    setSelectMode(false);
    setSelected([]);
    setBatchOp(null);
  };

  /** 全选/取消当前页（过滤后的已显示列表），不重复累计 */
  const toggleSelectPage = () => {
    const pageIds = items.map((m) => m.id);
    const allSel = pageIds.length > 0 && pageIds.every((id) => selected.includes(id));
    setSelected((prev) =>
      allSel
        ? prev.filter((id) => !pageIds.includes(id))
        : Array.from(new Set([...prev, ...pageIds]))
    );
  };

  /** 全选当前过滤条件下的全部结果（跨页）：一次拉取最多 500 条（最近创建优先）。
   *  搜索词非空时提示先清空——搜索链路无分页游标，无法可靠枚举全部命中。 */
  const selectAllResults = async () => {
    if (q.trim()) {
      showToast("搜索词模式下无法枚举全部结果，请先清空搜索词后再全选");
      return;
    }
    if (total <= 0) return;
    setSelectingAll(true);
    try {
      const take = Math.min(total, BATCH_LIMIT);
      const page = await apiMaterials.list({
        course_id: courseId === "" ? undefined : courseId,
        type: type || undefined,
        chapter: chapter || undefined,
        difficulty: difficulty || undefined,
        exam_focus: examFocus || undefined,
        tags: tagFilter.length ? tagFilter : undefined,
        tag_mode: tagMode,
        limit: take,
      });
      const ids = page.items.map((m) => m.id);
      setSelected((prev) => Array.from(new Set([...prev, ...ids])));
      showToast(
        total > BATCH_LIMIT
          ? `共 ${total} 条结果，已全选最近 ${ids.length} 条`
          : `已全选 ${ids.length} 份资料`
      );
    } catch (e) {
      alert((e as Error).message);
    } finally {
      setSelectingAll(false);
    }
  };

  const requestDelete = (ids: number[], label: string) => setConfirmDel({ ids, label });

  const doDelete = async () => {
    if (!confirmDel) return;
    const { ids } = confirmDel;
    setConfirmDel(null);
    try {
      if (ids.length === 1) {
        await apiMaterials.remove(ids[0]);
        showToast("已删除该资料");
      } else {
        const res = await apiMaterials.batchDelete(ids);
        showToast(res.deleted > 0 ? `已删除 ${res.deleted} 份资料` : "没有资料被删除");
      }
      if (selectMode) exitSelectMode();
      bump();
    } catch (e) {
      alert((e as Error).message);
    }
  };

  const semanticActive = mode !== "fts";

  return (
    <div className="space-y-5">
      <PageHeader
        title="资料库"
        desc={
          <>
            全文检索 · 语义检索 · 标签过滤 · 一键归档，共 {total} 条结果
            {truncated && items.length < total && (
              <span className="ml-1 text-slate-400">（已显示 {items.length} 条）</span>
            )}
            {semanticActive && semStatus && (
              <span className="ml-2 inline-flex items-center gap-1 rounded-full bg-violet-50 px-2 py-0.5 text-[11px] text-violet-600">
                <BrainCircuit size={12} />
                {semStatus.backend === "tfidf" ? "离线向量引擎" : `语义引擎 ${semStatus.model}`}
              </span>
            )}
          </>
        }
        actions={
          <button
            onClick={() => (selectMode ? exitSelectMode() : setSelectMode(true))}
            className={`flex items-center gap-1.5 rounded-xl border px-3 py-2 text-sm transition ${
              selectMode
                ? "border-brand-600 bg-brand-600 font-medium text-white shadow-sm"
                : "border-slate-200 bg-white text-slate-600 hover:border-brand-300 hover:bg-brand-50 hover:text-brand-600"
            }`}
            title="批量删除 / 批量改标签 / 批量换课程 / 批量归档设置"
          >
            <ListChecks size={15} />
            {selectMode ? "退出批量管理" : "批量管理"}
          </button>
        }
      />

      {/* 搜索 + 模式切换 + 过滤 */}
      <div className="flex flex-wrap gap-2">
        <div className="relative min-w-64 flex-1">
          <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
          <input
            className="w-full rounded-xl border border-slate-200 bg-white py-2.5 pl-9 pr-3 text-sm outline-none focus:border-brand-500"
            placeholder={
              semanticActive
                ? "试试自然语言：「那个讲死锁的例子」…"
                : "搜「死锁」「傅里叶」「实验报告」…"
            }
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
        </div>
        <div className="flex overflow-hidden rounded-xl border border-slate-200 bg-white text-sm">
          {(Object.keys(MODE_LABELS) as SearchMode[]).map((m) => (
            <button
              key={m}
              onClick={() => setMode(m)}
              className={`px-3.5 py-2.5 transition ${
                mode === m ? "bg-brand-600 font-medium text-white" : "text-slate-500 hover:bg-brand-50"
              }`}
            >
              {MODE_LABELS[m]}
            </button>
          ))}
        </div>
        <select
          className="rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-sm"
          value={courseId}
          onChange={(e) => {
            setLimit(PAGE_STEP);
            setCourseId(e.target.value === "" ? "" : Number(e.target.value));
          }}
        >
          <option value="">全部课程</option>
          {courses.map((c) => (
            <option key={c.id} value={c.id}>{c.name}</option>
          ))}
        </select>
        <select
          className="rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-sm"
          value={type}
          onChange={(e) => {
            setLimit(PAGE_STEP);
            setType(e.target.value);
          }}
        >
          <option value="">全部类型</option>
          {Object.entries(MATERIAL_TYPES).map(([k, v]) => (
            <option key={k} value={k}>{v}</option>
          ))}
        </select>
        {/* 章节过滤（独立字段，后端 SQL 下推） */}
        {chapterFacets.length > 0 && (
          <select
            className="rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-sm"
            value={chapter}
            onChange={(e) => {
              setLimit(PAGE_STEP);
              setChapter(e.target.value);
            }}
          >
            <option value="">全部章节</option>
            {chapterFacets.map((f) => (
              <option key={f.chapter} value={f.chapter}>
                {f.chapter}（{f.count}）
              </option>
            ))}
          </select>
        )}
        {/* 难度过滤 */}
        <select
          className="rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-sm"
          value={difficulty}
          onChange={(e) => {
            setLimit(PAGE_STEP);
            setDifficulty(e.target.value);
          }}
        >
          {Object.entries(DIFFICULTIES).map(([k, v]) => (
            <option key={k} value={k}>{k === "" ? "全部难度" : v}</option>
          ))}
        </select>
        {/* 考试重点开关 */}
        <button
          onClick={() => {
            setLimit(PAGE_STEP);
            setExamFocus((v) => !v);
          }}
          className={`flex items-center gap-1 rounded-xl border px-3 py-2.5 text-sm transition ${
            examFocus
              ? "border-red-300 bg-red-50 font-medium text-red-600"
              : "border-slate-200 bg-white text-slate-500 hover:bg-red-50 hover:text-red-500"
          }`}
          title="只看老师强调的考试重点资料"
        >
          <Star size={14} className={examFocus ? "fill-red-500 text-red-500" : ""} />
          考试重点
        </button>
      </div>

      {/* 标签云：后端全库聚合（带资料数），可多选；过滤在 SQL 内完成，不受当前页限制 */}
      {facets.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5">
          {facets.map((f) => {
            const active = tagFilter.includes(f.tag);
            return (
              <button
                key={f.tag}
                onClick={() => toggleTag(f.tag)}
                className={`flex items-center gap-1 rounded-full px-2.5 py-1 text-xs transition ${
                  active
                    ? "bg-brand-600 text-white"
                    : "bg-white text-slate-500 ring-1 ring-slate-200 hover:bg-brand-50"
                }`}
              >
                {f.tag}
                <span className={active ? "text-brand-100" : "text-slate-400"}>{f.count}</span>
              </button>
            );
          })}
          {tagFilter.length > 1 && (
            <div className="ml-1 flex overflow-hidden rounded-full border border-slate-200 bg-white text-[11px]">
              {(["any", "all"] as const).map((m) => (
                <button
                  key={m}
                  onClick={() => setTagMode(m)}
                  className={`px-2 py-1 transition ${
                    tagMode === m ? "bg-slate-800 text-white" : "text-slate-500 hover:bg-slate-50"
                  }`}
                >
                  {m === "any" ? "任一标签" : "同时含全部"}
                </button>
              ))}
            </div>
          )}
          {tagFilter.length > 0 && (
            <button
              onClick={() => setTagFilter([])}
              className="ml-1 text-[11px] text-slate-400 hover:text-red-500"
            >
              清空标签（{tagFilter.length}）
            </button>
          )}
        </div>
      )}

      {/* 批量操作工具栏：吸顶常驻，勾选后可批量删除 / 改标签 / 换课程 / 归档设置 */}
      {selectMode && (
        <div className="sticky top-14 z-20 md:top-0">
          <div className="flex flex-wrap items-center gap-2 rounded-2xl border border-brand-200/80 bg-gradient-to-r from-brand-50/95 via-white/95 to-violet-50/95 px-3 py-2.5 shadow-sm backdrop-blur">
            <span className="flex items-center gap-2 text-sm font-semibold text-brand-700">
              <span className="flex h-6 min-w-6 items-center justify-center rounded-full bg-brand-600 px-1.5 text-xs font-bold text-white">
                {selected.length}
              </span>
              已选
            </span>
            <div className="mx-1 hidden h-5 w-px bg-brand-200/60 sm:block" />
            <button
              onClick={toggleSelectPage}
              className="rounded-lg border border-brand-200 bg-white px-2.5 py-1.5 text-xs text-brand-700 transition hover:bg-brand-100"
            >
              {items.length > 0 && items.every((m) => selected.includes(m.id))
                ? "取消本页"
                : "全选本页"}
            </button>
            <button
              onClick={selectAllResults}
              disabled={selectingAll}
              title={
                q.trim()
                  ? "搜索词模式下无法枚举全部结果，请先清空搜索词"
                  : `全选当前过滤条件下的全部结果（单次最多 ${BATCH_LIMIT} 条）`
              }
              className="rounded-lg border border-brand-200 bg-white px-2.5 py-1.5 text-xs text-brand-700 transition hover:bg-brand-100 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {selectingAll ? "拉取中…" : `全选全部${total > 0 ? `（${total}）` : ""}`}
            </button>
            {selected.length > 0 && (
              <button
                onClick={() => setSelected([])}
                className="text-[11px] text-slate-400 hover:text-red-500"
                title="清空已选"
              >
                清空
              </button>
            )}
            <div className="ml-auto flex flex-wrap items-center gap-1.5">
              <button
                onClick={() => setBatchOp("tags")}
                disabled={!selected.length}
                className="flex items-center gap-1 rounded-lg bg-brand-600 px-2.5 py-1.5 text-xs font-medium text-white transition hover:bg-brand-700 disabled:opacity-40"
              >
                <Tags size={12} /> 改标签
              </button>
              <button
                onClick={() => setBatchOp("course")}
                disabled={!selected.length}
                className="flex items-center gap-1 rounded-lg bg-brand-600 px-2.5 py-1.5 text-xs font-medium text-white transition hover:bg-brand-700 disabled:opacity-40"
              >
                <FolderInput size={12} /> 换课程
              </button>
              <button
                onClick={() => setBatchOp("fields")}
                disabled={!selected.length}
                className="flex items-center gap-1 rounded-lg bg-indigo-600 px-2.5 py-1.5 text-xs font-medium text-white transition hover:bg-indigo-700 disabled:opacity-40"
              >
                <SlidersHorizontal size={12} /> 归档设置
              </button>
              <button
                onClick={() => requestDelete(selected, `${selected.length} 份资料`)}
                disabled={!selected.length}
                className="flex items-center gap-1 rounded-lg bg-red-600 px-2.5 py-1.5 text-xs font-medium text-white transition hover:bg-red-700 disabled:opacity-40"
              >
                <Trash2 size={12} /> 删除
              </button>
              <button
                onClick={exitSelectMode}
                className="rounded-lg px-2 py-1.5 text-xs text-brand-600 transition hover:bg-brand-100"
              >
                退出
              </button>
            </div>
          </div>
        </div>
      )}

      {loading && (
        <div className="flex items-center gap-2 text-sm text-slate-400">
          <Spinner /> 检索中…
        </div>
      )}

      {/* 卡片列表 */}
      {items.length === 0 && !loading ? (
        <Empty
          text={
            q
              ? `没有找到与「${q}」相关的资料${mode !== "fts" ? "（可切换回「全文」模式试试）" : ""}`
              : "资料库还是空的，去「智能入库」添加吧"
          }
        />
      ) : (
        <div className="grid gap-3 md:grid-cols-2">
          {items.map((m, i) => {
            const isSel = selected.includes(m.id);
            return (
              <div
                key={m.id}
                onClick={selectMode ? () => toggleSelect(m.id) : undefined}
                className={`group rounded-2xl border bg-white p-4 shadow-sm transition ${
                  selectMode
                    ? isSel
                      ? "cursor-pointer border-brand-400 bg-brand-50/70 shadow-md ring-1 ring-brand-300"
                      : "cursor-pointer border-slate-200 hover:border-brand-300 hover:bg-brand-50/40"
                    : "border-slate-200 hover:border-brand-200 hover:shadow"
                }`}
              >
                <div className="flex items-start gap-3">
                  {selectMode && (
                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation();
                        toggleSelect(m.id);
                      }}
                      aria-label={isSel ? "取消选择" : "选择"}
                      className={`mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-md border transition ${
                        isSel
                          ? "border-brand-600 bg-brand-600 text-white"
                          : "border-slate-300 bg-white text-transparent hover:border-brand-400"
                      }`}
                    >
                      <Check size={13} strokeWidth={3} />
                    </button>
                  )}
                  <div
                    className="mt-0.5 rounded-lg p-2 text-white"
                    style={{ backgroundColor: courseColor(m.course_name ?? "") }}
                  >
                    <FileText size={15} />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <h3 className="truncate text-sm font-semibold text-slate-800">{m.title}</h3>
                      <span className="shrink-0 rounded-md bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-500">
                        {MATERIAL_TYPES[m.type] ?? m.type}
                      </span>
                      {m.chapter && (
                        <span className="shrink-0 rounded-md bg-indigo-50 px-1.5 py-0.5 text-[10px] font-medium text-indigo-600">
                          {m.chapter}
                        </span>
                      )}
                      {m.difficulty && (
                        <span className={`shrink-0 rounded-md px-1.5 py-0.5 text-[10px] font-medium ${DIFF_COLORS[m.difficulty] ?? DIFF_COLORS[""]}`}>
                          {DIFFICULTIES[m.difficulty] ?? m.difficulty}
                        </span>
                      )}
                      {m.exam_focus && (
                        <span className="flex shrink-0 items-center gap-0.5 rounded-md bg-red-100 px-1.5 py-0.5 text-[10px] font-medium text-red-600">
                          <Star size={9} className="fill-red-500 text-red-500" />
                          考点
                        </span>
                      )}
                    </div>
                    <div className="mt-0.5 text-[11px] text-slate-400">
                      {m.course_name ?? "未分类"} · {fmtDT(m.created_at)}
                      {m.source === "file" && m.file_name && ` · ${m.file_name}`}
                    </div>
                    <Highlight text={snips[i]} />
                    {scores[i] != null && (
                      <div className="mt-1.5 inline-flex items-center gap-1 rounded-md bg-violet-50 px-1.5 py-0.5 text-[10px] font-medium text-violet-600">
                        相似度 {Math.round((scores[i] as number) * 100)}%
                      </div>
                    )}
                    {m.tags.length > 0 && (
                      <div className="mt-2 flex flex-wrap gap-1">
                        {m.tags.map((t) => (
                          <span key={t} className="rounded-full bg-brand-50 px-2 py-0.5 text-[10px] text-brand-700">
                            {t}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                  {!selectMode && (
                    <div className="flex shrink-0 gap-1 opacity-0 transition group-hover:opacity-100">
                      {m.file_path && (
                        <button
                          className="rounded-lg p-1.5 text-slate-400 hover:bg-brand-50 hover:text-brand-600"
                          title="预览原文件（PDF/PPT/图片）"
                          onClick={() => setPreviewing(m)}
                        >
                          <Eye size={14} />
                        </button>
                      )}
                      <button
                        className="rounded-lg p-1.5 text-slate-400 hover:bg-violet-50 hover:text-violet-600"
                        title="AI 提炼重点/考点笔记"
                        onClick={() => setRefining(m)}
                      >
                        <Wand2 size={14} />
                      </button>
                      <button
                        className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100"
                        onClick={() => setEditing(m)}
                      >
                        <Pencil size={14} />
                      </button>
                      <button
                        className="rounded-lg p-1.5 text-slate-400 hover:bg-red-50 hover:text-red-500"
                        onClick={() => requestDelete([m.id], `「${m.title}」`)}
                      >
                        <Trash2 size={14} />
                      </button>
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* 结果被 limit 截断时给出显式出口（总数来自后端过滤后的真实计数） */}
      {items.length > 0 && items.length < total && (
        <div className="flex flex-col items-center gap-1">
          <button
            onClick={() => setLimit((n) => n + PAGE_STEP)}
            disabled={loading}
            className="rounded-xl border border-slate-200 bg-white px-4 py-2 text-sm text-slate-600 transition hover:border-brand-400 hover:text-brand-600 disabled:opacity-50"
          >
            {loading ? "加载中…" : `显示更多（已显示 ${items.length} / ${total}）`}
          </button>
          <span className="text-[11px] text-slate-400">
            过滤与计数都在后端完成，标签筛选不会因为分页而漏结果
          </span>
        </div>
      )}

      {/* 编辑弹窗 */}
      {editing && (
        <Modal title="编辑资料" onClose={() => setEditing(null)}>
          <EditForm material={editing} onDone={() => { setEditing(null); bump(); }} />
        </Modal>
      )}

      {/* 原文件内嵌预览弹窗：PDF 原生查看器 / PPTX 纯前端渲染 / 图片 */}
      {previewing && (
        <Modal
          title={`预览：${previewing.title}`}
          onClose={() => setPreviewing(null)}
          xwide
        >
          <Suspense
            fallback={
              <div className="flex h-[62vh] items-center justify-center gap-2 text-sm text-slate-400">
                <Spinner /> 正在加载预览组件…
              </div>
            }
          >
            <MaterialPreview material={previewing} />
          </Suspense>
        </Modal>
      )}

      {/* 提炼笔记弹窗：AI 从资料全文提炼重点/考点笔记 */}
      {refining && (
        <Modal
          title={`提炼笔记：${refining.title}`}
          onClose={() => setRefining(null)}
          wide
        >
          {refining.text ? (
            <RefinePanel
              materialId={refining.id}
              materialTitle={refining.title}
              text={refining.text}
              autoRun
              onSaved={() => {
                setRefining(null);
                bump();
              }}
            />
          ) : (
            <div className="flex items-center gap-2 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2.5 text-xs leading-5 text-amber-700">
              <BrainCircuit size={14} className="shrink-0" />
              这份资料没有可提炼的全文（可能是扫描件 OCR 失败）。
              你可以去左侧「AI 笔记提炼」页粘贴文本自由提炼。
            </div>
          )}
        </Modal>
      )}

      {/* 批量改标签弹窗：追加 / 移除 / 覆盖 */}
      {batchOp === "tags" && (
        <Modal title={`批量改标签（${selected.length} 份资料）`} onClose={() => setBatchOp(null)}>
          <BatchTagsForm
            ids={selected}
            onSaved={(res) => {
              exitSelectMode();
              bump();
              showToast(res.updated > 0 ? `已更新 ${res.updated} 份资料的标签` : "标签未发生变化");
            }}
          />
        </Modal>
      )}

      {/* 批量换课程弹窗（可选移到未分类） */}
      {batchOp === "course" && (
        <Modal title={`批量换课程（${selected.length} 份资料）`} onClose={() => setBatchOp(null)}>
          <BatchCourseForm
            ids={selected}
            courses={courses}
            onSaved={(res) => {
              exitSelectMode();
              bump();
              showToast(res.updated > 0 ? `已移动 ${res.updated} 份资料` : "课程归属未发生变化");
            }}
          />
        </Modal>
      )}

      {/* 批量归档设置弹窗：章节 / 难度 / 考试重点 / 类型 */}
      {batchOp === "fields" && (
        <Modal title={`批量归档设置（${selected.length} 份资料）`} onClose={() => setBatchOp(null)}>
          <BatchFieldsForm
            ids={selected}
            onSaved={(res) => {
              exitSelectMode();
              bump();
              showToast(res.updated > 0 ? `已归档 ${res.updated} 份资料` : "归档字段未发生变化");
            }}
          />
        </Modal>
      )}

      {/* 删除确认弹窗（单条 / 批量共用，替代原生 confirm） */}
      {confirmDel && (
        <Modal title="删除确认" onClose={() => setConfirmDel(null)}>
          <div className="space-y-4">
            <div className="flex gap-3">
              <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-red-50 text-red-500">
                <Trash2 size={18} />
              </div>
              <div className="min-w-0">
                <p className="text-sm font-medium text-slate-800">
                  确定删除{confirmDel.ids.length === 1 ? `资料 ${confirmDel.label}` : `选中的 ${confirmDel.label}`}？
                </p>
                <p className="mt-1 text-xs leading-5 text-slate-500">
                  附件文件与间隔重复复习记录将一并清理；已提炼的笔记、自测组与任务会保留，
                  但会断开与这些资料的关联。此操作不可撤销。
                </p>
              </div>
            </div>
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setConfirmDel(null)}
                className="rounded-xl border border-slate-200 bg-white px-4 py-2 text-sm text-slate-600 transition hover:bg-slate-50"
              >
                取消
              </button>
              <button
                onClick={doDelete}
                className="flex items-center gap-1.5 rounded-xl bg-red-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-red-700"
              >
                <Trash2 size={14} /> 确认删除
              </button>
            </div>
          </div>
        </Modal>
      )}

      {/* 操作结果轻提示 */}
      {toast && (
        <div className="pointer-events-none fixed bottom-6 left-1/2 z-[60] -translate-x-1/2">
          <div className="flex items-center gap-2 rounded-full bg-slate-800/95 px-4 py-2 text-sm text-white shadow-lg backdrop-blur">
            <CheckCircle2 size={15} className="shrink-0 text-emerald-400" />
            {toast}
          </div>
        </div>
      )}
    </div>
  );
}

/** 批量改标签：追加（add）/ 移除（remove）/ 覆盖（set，留空 = 清空标签） */
function BatchTagsForm({
  ids,
  onSaved,
}: {
  ids: number[];
  onSaved: (res: MaterialBatchResult) => void;
}) {
  const [mode, setMode] = useState<"add" | "remove" | "set">("add");
  const [tags, setTags] = useState("");
  const [saving, setSaving] = useState(false);
  const parsed = tags.split(/[，,]/).map((s) => s.trim()).filter(Boolean);
  const canSave = !saving && (mode === "set" || parsed.length > 0);

  const hints: Record<"add" | "remove" | "set", string> = {
    add: "要追加的标签（逗号分隔，已有标签保持不变）",
    remove: "要移除的标签（逗号分隔，其它标签保持不变）",
    set: "覆盖后的完整标签（逗号分隔；留空 = 清空选中资料的全部标签）",
  };

  const save = async () => {
    if (!canSave) return;
    setSaving(true);
    try {
      const res = await apiMaterials.batchTags(ids, parsed, mode);
      onSaved(res);
    } catch (e) {
      alert((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-3">
      <div className="flex overflow-hidden rounded-xl border border-slate-200 text-xs">
        {(["add", "remove", "set"] as const).map((m) => (
          <button
            key={m}
            onClick={() => setMode(m)}
            className={`flex-1 px-3 py-2 transition ${
              mode === m ? "bg-brand-600 font-medium text-white" : "text-slate-500 hover:bg-brand-50"
            }`}
          >
            {m === "add" ? "追加标签" : m === "remove" ? "移除标签" : "覆盖标签"}
          </button>
        ))}
      </div>
      <label className="block text-xs text-slate-500">
        {hints[mode]}
        <input
          autoFocus
          className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2 text-sm"
          placeholder="如：期末，重点，易错"
          value={tags}
          onChange={(e) => setTags(e.target.value)}
        />
      </label>
      <p className="text-[11px] text-slate-400">
        将作用于 {ids.length} 份已选资料，标签会同步更新全文检索与语义索引。
      </p>
      <button
        onClick={save}
        disabled={!canSave}
        className="w-full rounded-xl bg-brand-600 py-2.5 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-50"
      >
        {saving ? "保存中…" : "保存"}
      </button>
    </div>
  );
}

/** 批量换课程：课程下拉 + 「未分类」选项（移除课程归属） */
function BatchCourseForm({
  ids,
  courses,
  onSaved,
}: {
  ids: number[];
  courses: Course[];
  onSaved: (res: MaterialBatchResult) => void;
}) {
  const [courseId, setCourseId] = useState<number | "none" | "">("");
  const [saving, setSaving] = useState(false);

  const save = async () => {
    if (courseId === "" || saving) return;
    setSaving(true);
    try {
      const res = await apiMaterials.batchCourse(ids, courseId === "none" ? null : courseId);
      onSaved(res);
    } catch (e) {
      alert((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-3">
      <label className="block text-xs text-slate-500">
        目标课程
        <select
          className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2 text-sm"
          value={courseId}
          onChange={(e) =>
            setCourseId(
              e.target.value === ""
                ? ""
                : e.target.value === "none"
                  ? "none"
                  : Number(e.target.value)
            )
          }
        >
          <option value="">请选择课程…</option>
          {courses.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
          <option value="none">未分类（移除课程归属）</option>
        </select>
      </label>
      <p className="text-[11px] text-slate-400">
        将把 {ids.length} 份已选资料移动到所选课程。
      </p>
      <button
        onClick={save}
        disabled={courseId === "" || saving}
        className="w-full rounded-xl bg-brand-600 py-2.5 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-50"
      >
        {saving ? "保存中…" : "保存"}
      </button>
    </div>
  );
}

/** 批量归档设置：章节 / 难度 / 考试重点 / 类型。
 *  每项独立开关——打开 = 该字段将批量应用；未打开的字段保持原样。 */
function BatchFieldsForm({
  ids,
  onSaved,
}: {
  ids: number[];
  onSaved: (res: MaterialBatchResult) => void;
}) {
  const [chapterOn, setChapterOn] = useState(false);
  const [chapter, setChapter] = useState("");
  const [diffOn, setDiffOn] = useState(false);
  const [difficulty, setDifficulty] = useState("");
  const [focusOn, setFocusOn] = useState(false);
  const [focusVal, setFocusVal] = useState<"mark" | "clear">("mark");
  const [typeOn, setTypeOn] = useState(false);
  const [type, setType] = useState("");
  const [saving, setSaving] = useState(false);

  const enabled = chapterOn || diffOn || focusOn || typeOn;
  const canSave = enabled && !saving;

  const save = async () => {
    if (!canSave) return;
    setSaving(true);
    try {
      const res = await apiMaterials.batchFields(ids, {
        chapter: chapterOn ? chapter.trim() : null,
        difficulty: diffOn ? difficulty : null,
        exam_focus: focusOn ? focusVal === "mark" : null,
        type: typeOn ? type : null,
      });
      onSaved(res);
    } catch (e) {
      alert((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-3">
      <p className="rounded-xl border border-brand-100 bg-brand-50/60 px-3 py-2 text-[11px] leading-5 text-brand-700">
        打开某项开关后填写/选择值，将批量应用到 {ids.length} 份已选资料；
        未打开的字段保持原样。章节/难度/考试重点会同步更新全文检索与复习计划加权。
      </p>

      {/* 章节 */}
      <div className={`rounded-xl border p-3 transition ${chapterOn ? "border-brand-300 bg-brand-50/50" : "border-slate-200"}`}>
        <label className="flex cursor-pointer items-center gap-2 text-sm font-medium text-slate-700">
          <input
            type="checkbox"
            className="h-4 w-4 accent-brand-600"
            checked={chapterOn}
            onChange={(e) => setChapterOn(e.target.checked)}
          />
          章节
        </label>
        <input
          disabled={!chapterOn}
          className="mt-2 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm outline-none focus:border-brand-400 disabled:bg-slate-50 disabled:text-slate-400"
          placeholder="如：第3章 内存管理（留空 = 清空章节）"
          value={chapter}
          onChange={(e) => setChapter(e.target.value)}
        />
      </div>

      {/* 难度 */}
      <div className={`rounded-xl border p-3 transition ${diffOn ? "border-brand-300 bg-brand-50/50" : "border-slate-200"}`}>
        <label className="flex cursor-pointer items-center gap-2 text-sm font-medium text-slate-700">
          <input
            type="checkbox"
            className="h-4 w-4 accent-brand-600"
            checked={diffOn}
            onChange={(e) => setDiffOn(e.target.checked)}
          />
          难度（复习计划按难度加权时长）
        </label>
        <select
          disabled={!diffOn}
          className="mt-2 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm outline-none focus:border-brand-400 disabled:bg-slate-50 disabled:text-slate-400"
          value={difficulty}
          onChange={(e) => setDifficulty(e.target.value)}
        >
          <option value="">未标注（清空难度）</option>
          {Object.entries(DIFFICULTIES).map(([k, v]) =>
            k === "" ? null : (
              <option key={k} value={k}>{v}</option>
            )
          )}
        </select>
      </div>

      {/* 考试重点 */}
      <div className={`rounded-xl border p-3 transition ${focusOn ? "border-brand-300 bg-brand-50/50" : "border-slate-200"}`}>
        <label className="flex cursor-pointer items-center gap-2 text-sm font-medium text-slate-700">
          <input
            type="checkbox"
            className="h-4 w-4 accent-brand-600"
            checked={focusOn}
            onChange={(e) => setFocusOn(e.target.checked)}
          />
          考试重点（复习计划优先分配时间）
        </label>
        <select
          disabled={!focusOn}
          className="mt-2 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm outline-none focus:border-brand-400 disabled:bg-slate-50 disabled:text-slate-400"
          value={focusVal}
          onChange={(e) => setFocusVal(e.target.value as "mark" | "clear")}
        >
          <option value="mark">标记为考试重点</option>
          <option value="clear">取消考试重点标记</option>
        </select>
      </div>

      {/* 类型 */}
      <div className={`rounded-xl border p-3 transition ${typeOn ? "border-brand-300 bg-brand-50/50" : "border-slate-200"}`}>
        <label className="flex cursor-pointer items-center gap-2 text-sm font-medium text-slate-700">
          <input
            type="checkbox"
            className="h-4 w-4 accent-brand-600"
            checked={typeOn}
            onChange={(e) => setTypeOn(e.target.checked)}
          />
          资料类型
        </label>
        <select
          disabled={!typeOn}
          className="mt-2 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm outline-none focus:border-brand-400 disabled:bg-slate-50 disabled:text-slate-400"
          value={type}
          onChange={(e) => setType(e.target.value)}
        >
          <option value="">请选择类型…</option>
          {Object.entries(MATERIAL_TYPES).map(([k, v]) => (
            <option key={k} value={k}>{v}</option>
          ))}
        </select>
      </div>

      <button
        onClick={save}
        disabled={!canSave}
        className="w-full rounded-xl bg-brand-600 py-2.5 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-50"
      >
        {saving ? "保存中…" : enabled ? "应用到选中资料" : "先打开至少一项设置"}
      </button>
    </div>
  );
}

function EditForm({ material, onDone }: { material: Material; onDone: () => void }) {
  const [title, setTitle] = useState(material.title);
  const [type, setType] = useState(material.type);
  const [chapter, setChapter] = useState(material.chapter ?? "");
  const [difficulty, setDifficulty] = useState(material.difficulty ?? "");
  const [examFocus, setExamFocus] = useState(!!material.exam_focus);
  const [tags, setTags] = useState(material.tags.join("，"));
  const [saving, setSaving] = useState(false);

  const save = async () => {
    setSaving(true);
    try {
      await apiMaterials.patch(material.id, {
        title,
        type,
        chapter: chapter.trim(),
        difficulty,
        exam_focus: examFocus,
        tags: tags.split(/[，,]/).map((s) => s.trim()).filter(Boolean),
      });
      onDone();
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-3">
      <label className="block text-xs text-slate-500">
        标题
        <input
          className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2 text-sm"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
        />
      </label>
      <label className="block text-xs text-slate-500">
        类型
        <select
          className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2 text-sm"
          value={type}
          onChange={(e) => setType(e.target.value)}
        >
          {Object.entries(MATERIAL_TYPES).map(([k, v]) => (
            <option key={k} value={k}>{v}</option>
          ))}
        </select>
      </label>
      <div className="grid grid-cols-2 gap-3">
        <label className="block text-xs text-slate-500">
          章节（独立字段，如「第3章 内存管理」）
          <input
            className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2 text-sm"
            placeholder="第3章 内存管理"
            value={chapter}
            onChange={(e) => setChapter(e.target.value)}
          />
        </label>
        <label className="block text-xs text-slate-500">
          难度（复习计划会按难度加权）
          <select
            className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2 text-sm"
            value={difficulty}
            onChange={(e) => setDifficulty(e.target.value)}
          >
            {Object.entries(DIFFICULTIES).map(([k, v]) => (
              <option key={k} value={k}>{v}</option>
            ))}
          </select>
        </label>
      </div>
      <label className="flex cursor-pointer items-center gap-2 rounded-xl border border-red-200 bg-red-50 px-3 py-2.5 text-xs text-red-600">
        <input
          type="checkbox"
          className="h-4 w-4 accent-red-500"
          checked={examFocus}
          onChange={(e) => setExamFocus(e.target.checked)}
        />
        考试重点（老师强调的考点/必考内容，复习计划优先分配时间）
      </label>
      <label className="block text-xs text-slate-500">
        标签（逗号分隔）
        <input
          className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2 text-sm"
          value={tags}
          onChange={(e) => setTags(e.target.value)}
        />
      </label>
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
