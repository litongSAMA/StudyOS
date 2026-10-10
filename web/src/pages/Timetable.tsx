import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  CalendarDays,
  CalendarPlus,
  ChevronLeft,
  ChevronRight,
  Clock,
  Info,
  MapPin,
  Plus,
  User,
  Trash2,
} from "lucide-react";
import { apiCourses, apiMisc } from "../api";
import { useApp } from "../App";
import type { Course, CourseSlot } from "../types";
import { Empty, Modal, PageHeader, Spinner } from "../components/ui";
import CourseEditor from "../components/CourseEditor";
import TimetableImport from "../components/TimetableImport";
import { isAlternatingWeeks, MAX_WEEKS } from "../lib/courses";
import CourseContextMenu from "../components/CourseContextMenu";

const DAY_NAMES = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"];

const GRID_START = 8 * 60; // 默认从 08:00 开始
const GRID_END = 22 * 60 + 30; // 到 22:30
const STEP = 30; // 半小时一格
const ROW_H = 26;
const WEEK_OPTIONS = MAX_WEEKS;

function timeToMin(t: string): number {
  const [h, m] = t.split(":").map(Number);
  return (h || 0) * 60 + (m || 0);
}

/** 周次列表 → 「每周 / 单周 / 双周 / 第1-16周 / 第1,3,5周」 */
function weekLabel(weeks: number[]): string {
  if (!weeks || weeks.length === 0) return "每周";
  const sorted = [...new Set(weeks)].sort((a, b) => a - b);
  if (isAlternatingWeeks(sorted)) return `${sorted[0] % 2 ? "单" : "双"}周（${sorted[0]}-${sorted[sorted.length - 1]}）`;
  const parts: string[] = [];
  let i = 0;
  while (i < sorted.length) {
    let j = i;
    while (j + 1 < sorted.length && sorted[j + 1] === sorted[j] + 1) j++;
    parts.push(j > i ? `${sorted[i]}-${sorted[j]}` : `${sorted[i]}`);
    i = j + 1;
  }
  return parts.join(",");
}

/** 某时段是否在指定周次上课（未注明周次 → 每周都上） */
function slotActiveInWeek(slot: CourseSlot, week: number | "all"): boolean {
  if (week === "all") return true;
  if (!slot.weeks || slot.weeks.length === 0) return true;
  return slot.weeks.includes(week);
}

/** 学期锚点 → 当前是第几周（未设置/未开学返回 null） */
function currentWeekOf(anchor: string | null | undefined): number | null {
  if (!anchor) return null;
  const start = new Date(`${anchor}T00:00:00`);
  if (Number.isNaN(start.getTime())) return null;
  const diffDays = Math.floor((Date.now() - start.getTime()) / 86_400_000);
  if (diffDays < 0) return null;
  return Math.floor(diffDays / 7) + 1;
}

/** 全部时段的时间跨度（网格边界，含无时段时的默认范围） */
function gridBounds(courses: Course[]): { start: number; end: number } {
  let start = GRID_START;
  let end = GRID_END;
  for (const c of courses) {
    for (const s of c.slots) {
      start = Math.min(start, Math.floor(timeToMin(s.start) / STEP) * STEP);
      end = Math.max(end, Math.ceil(timeToMin(s.end) / STEP) * STEP);
    }
  }
  return { start, end };
}

export default function Timetable() {
  const { bump } = useApp();
  const [courses, setCourses] = useState<Course[]>([]);
  const [semesterStart, setSemesterStart] = useState("");
  const [week, setWeek] = useState<number | "all">("all");
  // 本页直接导入 / 新增课表：设置页的入口保留不变，这里不必再跳转
  const [showImport, setShowImport] = useState(false);
  const [showAdd, setShowAdd] = useState(false);
  const [editingCourse, setEditingCourse] = useState<Course | null>(null);
  const [contextMenu, setContextMenu] = useState<{ course: Course; x: number; y: number } | null>(null);
  const [deletingCourse, setDeletingCourse] = useState<Course | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState("");
  const [pageError, setPageError] = useState("");
  // 当前时刻（分钟），每 30s 刷新，用于「现在」时间线
  const [nowMin, setNowMin] = useState(() => {
    const d = new Date();
    return d.getHours() * 60 + d.getMinutes();
  });
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    apiCourses.list().then(setCourses).catch(console.error);
    apiMisc
      .getSettings()
      .then((s) => setSemesterStart(s.semester_start ?? ""))
      .catch(console.error);
  }, []);

  /** 导入/新增后重新拉课表（并广播数据变更，首页等联动刷新） */
  const reload = async () => {
    try {
      setCourses(await apiCourses.list());
      setPageError("");
      bump();
    } catch (e) {
      setPageError(`课表刷新失败：${(e as Error).message}`);
    }
  };

  const deleteCourse = async () => {
    if (!deletingCourse || deleting) return;
    setDeleting(true);
    setDeleteError("");
    try {
      await apiCourses.remove(deletingCourse.id);
      setCourses((current) => current.filter((c) => c.id !== deletingCourse.id));
      setDeletingCourse(null);
      bump();
    } catch (e) {
      setDeleteError((e as Error).message);
    } finally {
      setDeleting(false);
    }
  };

  useEffect(() => {
    const id = window.setInterval(() => {
      const d = new Date();
      setNowMin(d.getHours() * 60 + d.getMinutes());
    }, 30_000);
    return () => window.clearInterval(id);
  }, []);

  // 有效学期锚点：全局设置 > 最早开学的课程（与「第N周」换算口径一致）
  const anchor = useMemo(() => {
    if (semesterStart) return semesterStart;
    const dates = courses
      .map((c) => c.start_date)
      .filter((d): d is string => !!d);
    return dates.length ? dates.sort()[0] : "";
  }, [semesterStart, courses]);

  // 有课表时段的课程
  const withSlots = useMemo(() => courses.filter((c) => c.slots?.length), [courses]);
  const totalSlots = useMemo(
    () => withSlots.reduce((n, c) => n + c.slots.length, 0),
    [withSlots],
  );

  const currentWeek = useMemo(() => currentWeekOf(anchor || null), [anchor]);

  // 默认选中「当前周」；无锚点时只能看全部
  useEffect(() => {
    if (!anchor) setWeek("all");
    else if (currentWeek != null) setWeek(currentWeek);
    // 只在锚点就绪后初始化一次
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [anchor]);

  const bounds = useMemo(() => gridBounds(withSlots), [withSlots]);
  const rowCount = (bounds.end - bounds.start) / STEP;
  const hourLines: number[] = [];
  for (let t = Math.ceil(bounds.start / 60) * 60; t <= bounds.end; t += 60) hourLines.push(t);

  const today = new Date();
  // getDay(): 0=周日…6=周六 → 1=周一…7=周日
  const todayCol = (today.getDay() + 6) % 7 + 1;
  const todayLabel = `${today.getMonth() + 1}月${today.getDate()}日`;

  // 本周一~周日的日期数字（仅在查看当前周/全部周次时展示，避免与学期周次错位）
  const weekDates = useMemo(() => {
    const monday = new Date(today);
    monday.setDate(today.getDate() - (todayCol - 1));
    return DAY_NAMES.map((_, i) => {
      const d = new Date(monday);
      d.setDate(monday.getDate() + i);
      return d.getDate();
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [todayCol]);
  const showDates = week === "all" || week === currentWeek;

  // 「现在」时间线在网格中的纵向位置（落在显示区间内才显示）
  const nowOffset = ((nowMin - bounds.start) / STEP) * ROW_H;
  const showNowLine = nowOffset >= 0 && nowOffset <= rowCount * ROW_H;

  // 首次渲染后把网格滚动到当前时间附近
  useEffect(() => {
    if (!showNowLine || !scrollRef.current) return;
    const el = scrollRef.current;
    const target = Math.max(0, nowOffset - el.clientHeight / 3);
    el.scrollTo({ top: target, behavior: "smooth" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [withSlots.length]);

  // 本周实际上课的时段数（用于统计；「全部周次」时不过滤）
  const activeCount = useMemo(() => {
    if (week === "all") return totalSlots;
    let n = 0;
    for (const c of withSlots) for (const s of c.slots) if (slotActiveInWeek(s, week)) n++;
    return n;
  }, [withSlots, week, totalSlots]);

  const stepWeek = (delta: number) => {
    const cur = week === "all" ? (delta < 0 ? 1 : 0) : week;
    const next = clampWeek(cur + delta);
    setWeek(next);
  };
  const clampWeek = (w: number) => Math.min(WEEK_OPTIONS, Math.max(1, w));

  return (
    <div className="space-y-6">
      <PageHeader
        title={
          <>
            <CalendarDays size={20} className="text-brand-500" />
            周课表
          </>
        }
        desc="每周课程一目了然——含单双周、上课地点与任课老师；导入与新增课表就在本页完成"
        actions={
          <>
            <button
              onClick={() => setShowImport(true)}
              className="flex items-center gap-1.5 rounded-xl bg-brand-600 px-3.5 py-2 text-xs font-medium text-white transition hover:bg-brand-700"
            >
              <CalendarPlus size={14} />
              导入课表
            </button>
            <button
              onClick={() => setShowAdd(true)}
              className="flex items-center gap-1.5 rounded-xl border border-brand-200 bg-brand-50 px-3.5 py-2 text-xs font-medium text-brand-700 transition hover:border-brand-400"
            >
              <Plus size={14} />
              新增课程
            </button>
          </>
        }
      />

      {pageError && <div role="alert" className="flex items-center gap-3 rounded-xl bg-red-50 px-3 py-2 text-xs text-red-600">
        {pageError}<button type="button" onClick={reload} className="underline">重试刷新</button>
      </div>}

      {/* 控制条：周次切换 + 今日 + 统计 */}
      {withSlots.length > 0 && (
        <div className="flex flex-wrap items-center gap-3 rounded-2xl border border-slate-200 bg-white p-3.5 shadow-sm">
          <div className="flex items-center gap-1.5">
              <button
                onClick={() => stepWeek(-1)}
                disabled={week !== "all" && week <= 1}
                className="flex h-8 w-8 items-center justify-center rounded-lg text-slate-500 transition hover:bg-slate-100 disabled:opacity-30"
                title="上一周"
              >
                <ChevronLeft size={16} />
              </button>
              <select
                value={week === "all" ? "all" : String(week)}
                onChange={(e) =>
                  setWeek(e.target.value === "all" ? "all" : Number(e.target.value))
                }
                className="rounded-xl border border-slate-200 bg-white px-3 py-1.5 text-sm outline-none transition focus:border-brand-500"
              >
                <option value="all">全部周次</option>
                {currentWeek != null && (
                  <option value={String(currentWeek)}>第 {currentWeek} 周（当前）</option>
                )}
                {Array.from({ length: WEEK_OPTIONS }, (_, i) => i + 1)
                  .filter((w) => w !== currentWeek)
                  .map((w) => (
                    <option key={w} value={String(w)}>
                      第 {w} 周
                    </option>
                  ))}
              </select>
              <button
                onClick={() => stepWeek(1)}
                disabled={week !== "all" && week >= WEEK_OPTIONS}
                className="flex h-8 w-8 items-center justify-center rounded-lg text-slate-500 transition hover:bg-slate-100 disabled:opacity-30"
                title="下一周"
              >
                <ChevronRight size={16} />
              </button>
              {currentWeek != null && week !== currentWeek && (
                <button
                  onClick={() => setWeek(currentWeek)}
                  className="ml-1 rounded-lg bg-brand-50 px-2.5 py-1.5 text-xs font-medium text-brand-700 transition hover:bg-brand-100"
                >
                  回到本周
                </button>
              )}
          </div>

          <span className="ml-auto inline-flex items-center gap-1.5 text-xs text-slate-500">
            <CalendarDays size={13} className="text-brand-500" />
            {todayLabel} · 周{DAY_NAMES[todayCol - 1][1]}
            {anchor && currentWeek != null && (
              <span className="ml-1 rounded-md bg-brand-50 px-1.5 py-0.5 font-medium text-brand-600">
                当前第 {currentWeek} 周
              </span>
            )}
          </span>

          <span className="inline-flex items-center gap-1 rounded-lg bg-slate-50 px-2.5 py-1 text-[11px] text-slate-500">
            {withSlots.length} 门课 · {week === "all" ? totalSlots : activeCount} 个时段
          </span>
        </div>
      )}

      {/* 课程图例 */}
      {withSlots.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {withSlots.map((c) => (
            <span
              key={c.id}
              className="inline-flex items-center gap-1.5 rounded-full bg-white px-2.5 py-1 text-[11px] text-slate-600 ring-1 ring-slate-200 transition hover:ring-slate-300"
              title={c.location ? `默认地点：${c.location}` : ""}
            >
              <span
                className="h-2 w-2 rounded-full ring-2 ring-white"
                style={{ backgroundColor: c.color, boxShadow: `0 0 0 1px ${c.color}55` }}
              />
              {c.name}
              {c.teacher && <span className="text-slate-400">{c.teacher}</span>}
            </span>
          ))}
        </div>
      )}

      {withSlots.length === 0 ? (
        <div>
          <Empty text="还没有带课表的课程" />
          <div className="mt-4 flex flex-wrap items-center justify-center gap-2">
            <button
              onClick={() => setShowImport(true)}
              className="inline-flex items-center gap-1.5 rounded-xl bg-brand-600 px-5 py-2 text-sm font-medium text-white transition hover:bg-brand-700"
            >
              <CalendarPlus size={15} />
              导入课表
            </button>
            <button
              onClick={() => setShowAdd(true)}
              className="inline-flex items-center gap-1.5 rounded-xl border border-slate-200 bg-white px-5 py-2 text-sm font-medium text-slate-600 transition hover:border-brand-300 hover:text-brand-600"
            >
              <Plus size={15} />
              手动新增课程
            </button>
          </div>
          <p className="mt-2 text-center text-[11px] text-slate-400">
            支持教务系统 Excel/CSV/PDF/网页/Word 课表、iCal、JSON 与课表截图（OCR + AI 兜底）；也可
            <Link to="/settings" className="mx-0.5 text-brand-600 hover:underline">
              去「设置 → 我的课程」
            </Link>
            导入，或让 AI 助手代劳
          </p>
        </div>
      ) : (
        <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
          <div
            ref={scrollRef}
            className="max-h-[72vh] overflow-auto p-4"
          >
            <div className="min-w-[720px]">
              {/* 表头：星期 + 日期 + 今日高亮 */}
              <div
                className="grid"
                style={{ gridTemplateColumns: `52px repeat(7, 1fr)` }}
              >
                <div />
                {DAY_NAMES.map((d, i) => {
                  const isToday = i + 1 === todayCol;
                  const isWeekend = i >= 5;
                  return (
                    <div key={d} className="pb-2 text-center">
                      <div
                        className={`mx-auto flex w-fit flex-col items-center rounded-xl px-3 py-1 transition ${
                          isToday
                            ? "bg-brand-600 text-white shadow-sm shadow-brand-500/30"
                            : isWeekend
                              ? "text-slate-400"
                              : "text-slate-600"
                        }`}
                      >
                        <span className="text-xs font-semibold leading-none">{d}</span>
                        {showDates && (
                          <span
                            className={`mt-0.5 text-[10px] leading-none ${
                              isToday ? "text-white/80" : "text-slate-400"
                            }`}
                          >
                            {weekDates[i]}日
                          </span>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>

              <div
                className="grid"
                style={{ gridTemplateColumns: `52px repeat(7, 1fr)` }}
              >
                {/* 时间轴 */}
                <div className="relative" style={{ height: rowCount * ROW_H }}>
                  {hourLines.map((t) => (
                    <span
                      key={t}
                      className="absolute right-1.5 -translate-y-1/2 text-[10px] tabular-nums text-slate-400"
                      style={{ top: ((t - bounds.start) / STEP) * ROW_H }}
                    >
                      {String(Math.floor(t / 60)).padStart(2, "0")}:00
                    </span>
                  ))}
                  {/* 现在时刻标记 */}
                  {showNowLine && (
                    <span
                      className="absolute right-0 z-10 -translate-y-1/2 rounded-l bg-red-500 px-1 text-[9px] font-medium leading-4 text-white"
                      style={{ top: nowOffset }}
                    >
                      {String(Math.floor(nowMin / 60)).padStart(2, "0")}:
                      {String(nowMin % 60).padStart(2, "0")}
                    </span>
                  )}
                </div>

                {/* 每天一列，课程块绝对定位 */}
                {DAY_NAMES.map((_, dayIdx) => {
                  const isToday = dayIdx + 1 === todayCol;
                  const isWeekend = dayIdx >= 5;
                  return (
                    <div
                      key={dayIdx}
                      className={`relative border-l ${
                        isToday
                          ? "border-brand-200 bg-brand-50/40"
                          : isWeekend
                            ? "border-slate-100 bg-slate-50/40"
                            : "border-slate-100"
                      }`}
                      style={{ height: rowCount * ROW_H }}
                    >
                      {hourLines.map((t) => (
                        <div
                          key={t}
                          className="absolute inset-x-0 border-t border-slate-100"
                          style={{ top: ((t - bounds.start) / STEP) * ROW_H }}
                        />
                      ))}
                      {/* 现在时间线（今天列高亮，其余列浅线） */}
                      {showNowLine && (
                        <div
                          className={`absolute inset-x-0 z-10 border-t ${
                            isToday ? "border-red-500" : "border-red-300/60"
                          }`}
                          style={{ top: nowOffset }}
                        >
                          {isToday && (
                            <span className="absolute -left-1 -top-[3px] h-1.5 w-1.5 rounded-full bg-red-500" />
                          )}
                        </div>
                      )}
                      {withSlots.flatMap((c) =>
                        c.slots
                          .filter((s) => s.weekday === dayIdx + 1)
                          .map((s, si) => {
                            const active = slotActiveInWeek(s, week);
                            const top =
                              ((timeToMin(s.start) - bounds.start) / STEP) * ROW_H;
                            const height = Math.max(
                              ((timeToMin(s.end) - timeToMin(s.start)) / STEP) * ROW_H - 3,
                              24,
                            );
                            const loc = s.location || c.location;
                            const tea = s.teacher || c.teacher;
                            return (
                              <div
                                key={`${c.id}-${si}`}
                                role="button"
                                tabIndex={0}
                                aria-label={`${c.name} ${DAY_NAMES[s.weekday - 1]} ${s.start}-${s.end}，打开课程操作`}
                                aria-haspopup="menu"
                                onContextMenu={(e) => {
                                  e.preventDefault();
                                  setContextMenu({ course: c, x: e.clientX, y: e.clientY });
                                }}
                                onClick={(e) => {
                                  const rect = e.currentTarget.getBoundingClientRect();
                                  setContextMenu({ course: c, x: rect.left, y: rect.top + 24 });
                                }}
                                onKeyDown={(e) => {
                                  if (["Enter", " ", "ContextMenu"].includes(e.key) || (e.shiftKey && e.key === "F10")) {
                                    e.preventDefault();
                                    const rect = e.currentTarget.getBoundingClientRect();
                                    setContextMenu({ course: c, x: rect.left, y: rect.top + 24 });
                                  }
                                }}
                                className={`group absolute inset-x-1 cursor-pointer overflow-hidden rounded-lg px-1.5 py-1 text-white shadow-sm ring-1 ring-black/5 transition-all duration-150 hover:z-20 hover:shadow-md focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500 ${
                                  active
                                    ? "hover:-translate-y-0.5"
                                    : "opacity-30"
                                }`}
                                style={{ top, height, backgroundColor: c.color }}
                                title={`${c.name}\n${s.start}-${s.end} · ${weekLabel(s.weeks)}${
                                  loc ? `\n地点：${loc}` : ""
                                }${tea ? `\n老师：${tea}` : ""}\n右键或点击可修改、删除课程`}
                              >
                                {/* 顶部高光，增加层次 */}
                                <span className="pointer-events-none absolute inset-x-0 top-0 h-1/2 bg-gradient-to-b from-white/20 to-transparent" />
                                <div className="relative flex items-center gap-1">
                                  <span className="truncate text-[10px] font-semibold leading-tight">
                                    {c.name}
                                  </span>
                                  {active && week !== "all" && (
                                    <span className="ml-auto shrink-0 rounded bg-white/25 px-1 text-[8px] leading-3">
                                      本周
                                    </span>
                                  )}
                                </div>
                                {height >= 40 && (
                                  <div className="relative mt-0.5 flex items-center gap-0.5 text-[9px] leading-tight opacity-90">
                                    <Clock size={8} className="shrink-0" />
                                    <span className="tabular-nums">
                                      {s.start}-{s.end}
                                    </span>
                                  </div>
                                )}
                                {loc && height >= 54 && (
                                  <div className="relative mt-0.5 flex items-center gap-0.5 truncate text-[9px] leading-tight opacity-85">
                                    <MapPin size={8} className="shrink-0" />
                                    <span className="truncate">{loc}</span>
                                  </div>
                                )}
                                {tea && height >= 68 && (
                                  <div className="relative mt-0.5 flex items-center gap-0.5 truncate text-[9px] leading-tight opacity-80">
                                    <User size={8} className="shrink-0" />
                                    <span className="truncate">{tea}</span>
                                  </div>
                                )}
                                {height >= 82 && (
                                  <div className="relative mt-0.5 truncate text-[8px] leading-tight opacity-75">
                                    {weekLabel(s.weeks)}
                                  </div>
                                )}
                              </div>
                            );
                          }),
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* 说明 */}
      {withSlots.length > 0 && (
        <div className="flex items-start gap-2 rounded-xl bg-slate-50 px-3.5 py-2.5 text-[11px] leading-5 text-slate-500">
          <Info size={14} className="mt-0.5 shrink-0 text-brand-500" />
          <span>
            红色横线为当前时刻；浅色块 = 本周不上课（未列入该周次），「全部周次」显示完整课表。
            新增课程时可输入或逐周点选自定义周次，每个上课时段独立设置。更新或补录课表用右上角「导入课表 / 新增课程」即可。
            右键或点击课程块可修改、删除整门课程。
          </span>
        </div>
      )}

      {/* 导入课表：与「设置 → 我的课程 → 导入课表」同一套接口与合并规则 */}
      {showImport && (
        <TimetableImport onClose={() => setShowImport(false)} onImported={reload} />
      )}

      {/* 新增课程：直接在本页手动补录课程与上课时段 */}
      {showAdd && (
        <CourseEditor
          courses={courses}
          anchor={anchor}
          onClose={() => setShowAdd(false)}
          onSaved={reload}
        />
      )}
      {contextMenu && <CourseContextMenu
        x={contextMenu.x} y={contextMenu.y} name={contextMenu.course.name}
        onClose={() => setContextMenu(null)}
        onEdit={() => { setEditingCourse(contextMenu.course); setContextMenu(null); }}
        onDelete={() => { setDeletingCourse(contextMenu.course); setDeleteError(""); setContextMenu(null); }}
      />}
      {editingCourse && <CourseEditor
        key={editingCourse.id} course={editingCourse} courses={courses} anchor={anchor}
        onClose={() => setEditingCourse(null)} onSaved={reload}
      />}
      {deletingCourse && <Modal title="删除课程" onClose={() => { if (!deleting) setDeletingCourse(null); }}>
        <p className="text-sm leading-6 text-slate-700">
          确定删除课程「{deletingCourse.name}」及其全部 {deletingCourse.slots?.length ?? 0} 个上课时段？
        </p>
        <p className="mt-2 text-xs leading-5 text-slate-500">课程关联的资料和任务会保留，仅解除课程关联。删除后无法撤销。</p>
        {deleteError && <p role="alert" className="mt-3 text-xs text-red-500">{deleteError}</p>}
        <div className="mt-5 flex justify-end gap-2">
          <button type="button" disabled={deleting} onClick={() => setDeletingCourse(null)} className="rounded-xl border border-slate-200 px-4 py-2 text-sm text-slate-600 disabled:opacity-50">取消</button>
          <button type="button" disabled={deleting} onClick={deleteCourse} className="flex items-center gap-1.5 rounded-xl bg-red-500 px-4 py-2 text-sm font-medium text-white hover:bg-red-600 disabled:opacity-50">
            {deleting ? <Spinner /> : <Trash2 size={15} />}{deleting ? "删除中…" : "确认删除"}
          </button>
        </div>
      </Modal>}
    </div>
  );
}
