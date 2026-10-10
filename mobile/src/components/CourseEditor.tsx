import { useMemo, useState } from "react";
import { CalendarPlus, Check, Info, Plus, Trash2 } from "lucide-react";
import { apiCourses } from "../api";
import { COURSE_PALETTE, mergeSlots, parseWeeks, weeksHint } from "../lib/courses";
import type { Course, CourseSlot } from "../types";
import { Modal, Spinner } from "./ui";

const WEEKDAY_NAMES = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"];

/** 表单里的时段草稿：周次保留用户原样输入（如「1-16单」），保存时才解析 */
interface SlotDraft {
  weekday: number;
  start: string;
  end: string;
  weeks: string;
  location: string;
}

const newSlot = (weekday = 1): SlotDraft => ({
  weekday,
  start: "08:00",
  end: "09:40",
  weeks: "1-16",
  location: "",
});

const toMin = (t: string): number => {
  const [h, m] = (t || "").split(":").map(Number);
  return (h || 0) * 60 + (m || 0);
};

/** 表单初值：学期取已有课程里最新的那个、开学日期沿用课表页的锚点（全局设置 > 最早开学的课程），少让用户重复填 */
function defaults(courses: Course[], anchor: string) {
  const semester = courses.map((c) => c.semester).filter(Boolean).sort().pop() ?? "";
  return { semester, startDate: anchor };
}

export default function CourseEditor({
  courses,
  anchor = "",
  onClose,
  onSaved,
}: {
  /** 本机已有课程：用于「同名课程自动合并」提示与默认学期 */
  courses: Course[];
  /** 课表页的学期锚点（全局开学日期 > 最早开学的课程） */
  anchor?: string;
  onClose: () => void;
  onSaved: () => void;
}) {
  const initial = useMemo(() => defaults(courses, anchor), [courses, anchor]);
  const [name, setName] = useState("");
  const [teacher, setTeacher] = useState("");
  const [location, setLocation] = useState("");
  const [semester, setSemester] = useState(initial.semester);
  const [startDate, setStartDate] = useState(initial.startDate);
  // 默认配色取「下一支」调色板色（与导入时的自动配色同一顺序）
  const [color, setColor] = useState(() => COURSE_PALETTE[courses.length % COURSE_PALETTE.length]);
  const [slots, setSlots] = useState<SlotDraft[]>([newSlot()]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState<{ text: string } | null>(null);

  const existing = useMemo(
    () => courses.find((c) => c.name.trim() === name.trim() && name.trim() !== ""),
    [courses, name],
  );

  const setSlot = (index: number, patch: Partial<SlotDraft>) =>
    setSlots((prev) => prev.map((s, i) => (i === index ? { ...s, ...patch } : s)));

  /** 时段的解析结果（周次/时刻校验）：有错就不让保存，错误信息挂在对应行上 */
  const parsed = useMemo(
    () =>
      slots.map((s) => {
        let weeks: number[] = [];
        let err = "";
        try {
          weeks = parseWeeks(s.weeks);
        } catch (e) {
          err = (e as Error).message;
        }
        if (!err && (!s.start || !s.end)) err = "请填写开始与结束时间";
        else if (!err && toMin(s.end) <= toMin(s.start)) err = "结束时间要晚于开始时间";
        return { weeks, err };
      }),
    [slots],
  );
  const slotError = parsed.find((p) => p.err)?.err ?? "";

  const save = async () => {
    if (!name.trim()) {
      setError("请填写课程名，如「操作系统」");
      return;
    }
    if (slotError) {
      setError(slotError);
      return;
    }
    setBusy(true);
    setError("");
    const parsedSlots: CourseSlot[] = slots.map((s, i) => ({
      weekday: s.weekday,
      start: s.start,
      end: s.end,
      weeks: parsed[i].weeks,
      location: s.location.trim(),
      teacher: "",
    }));
    try {
      if (existing) {
        // 同名课程：把时段/老师/地点合并进去（与导入同一套合并规则），不重复建课
        const patch: Partial<Course> = { slots: mergeSlots(existing.slots || [], parsedSlots) };
        if (teacher.trim()) patch.teacher = teacher.trim();
        if (location.trim()) patch.location = location.trim();
        if (semester.trim()) patch.semester = semester.trim();
        if (startDate) patch.start_date = startDate;
        const saved = await apiCourses.update(existing.id, patch);
        const added = (saved.slots?.length ?? 0) - (existing.slots?.length ?? 0);
        setDone({
          text: `已把 ${parsedSlots.length} 个时段合并到「${saved.name}」${
            added > 0 ? `（新增 ${added} 个时段）` : "（时段已存在，自动并集周次）"
          }`,
        });
      } else {
        const created = await apiCourses.create({
          name: name.trim(),
          teacher: teacher.trim(),
          location: location.trim(),
          semester: semester.trim(),
          start_date: startDate || null,
          color,
          slots: parsedSlots,
        });
        setDone({
          text: parsedSlots.length
            ? `课程「${created.name}」已加入周课表（${parsedSlots.length} 个时段）`
            : `课程「${created.name}」已保存：补上时段后就会出现在周课表上`,
        });
      }
      onSaved();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  if (done) {
    return (
      <Modal title="新增课程" onClose={onClose}>
        <div className="py-6 text-center">
          <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-full bg-emerald-100 text-emerald-600">
            <Check size={24} />
          </div>
          <p className="text-base font-semibold text-slate-800">已保存 🎉</p>
          <p className="mt-1 text-sm text-slate-500">{done.text}</p>
          <div className="mt-5 flex items-center justify-center gap-2">
            <button
              onClick={() => {
                setName("");
                setTeacher("");
                setLocation("");
                setSlots([newSlot()]);
                setDone(null);
                setError("");
              }}
              className="rounded-xl border border-slate-200 px-4 py-2 text-sm text-slate-600 hover:border-brand-300 hover:text-brand-600"
            >
              继续新增
            </button>
            <button
              onClick={onClose}
              className="rounded-xl bg-brand-600 px-6 py-2 text-sm font-medium text-white hover:bg-brand-700"
            >
              完成
            </button>
          </div>
        </div>
      </Modal>
    );
  }

  return (
    <Modal title="新增课程" onClose={onClose} wide>
      <form
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          void save();
        }}
      >
        <p className="text-xs leading-5 text-slate-500">
          填课程名与上课时段，保存后立即出现在周课表上；
          <b>同名课程自动合并</b>（同「星期 + 时间」的时段并集周次），不会重复建课。
        </p>

        {/* 课程基本信息 */}
        <div className="grid gap-2 md:grid-cols-2">
          <label className="text-xs text-slate-500">
            课程名 <span className="text-red-400">*</span>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="如：高等数学"
              autoFocus
              className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2 text-sm text-slate-700 outline-none transition focus:border-brand-500"
            />
          </label>
          <label className="text-xs text-slate-500">
            任课老师
            <input
              value={teacher}
              onChange={(e) => setTeacher(e.target.value)}
              placeholder="如：孙何军"
              className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2 text-sm text-slate-700 outline-none transition focus:border-brand-500"
            />
          </label>
          <label className="text-xs text-slate-500">
            默认地点
            <input
              value={location}
              onChange={(e) => setLocation(e.target.value)}
              placeholder="如：N-A103（各时段可单独覆盖）"
              className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2 text-sm text-slate-700 outline-none transition focus:border-brand-500"
            />
          </label>
          <label className="text-xs text-slate-500">
            学期
            <input
              value={semester}
              onChange={(e) => setSemester(e.target.value)}
              placeholder="如：2025-2026-1"
              className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2 text-sm text-slate-700 outline-none transition focus:border-brand-500"
            />
          </label>
          <label className="text-xs text-slate-500">
            开学日期（用于「第N周」换算）
            <input
              type="date"
              value={startDate}
              onChange={(e) => setStartDate(e.target.value)}
              className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2 text-sm text-slate-700 outline-none transition focus:border-brand-500"
            />
          </label>
          <div className="text-xs text-slate-500">
            颜色
            <div className="mt-2 flex flex-wrap gap-1.5">
              {COURSE_PALETTE.map((c) => (
                <button
                  key={c}
                  type="button"
                  aria-label={`选择颜色 ${c}`}
                  aria-pressed={color === c}
                  onClick={() => setColor(c)}
                  className={`h-6 w-6 rounded-full ring-2 transition ${
                    color === c ? "ring-slate-700" : "ring-transparent hover:ring-slate-300"
                  }`}
                  style={{ backgroundColor: c }}
                />
              ))}
            </div>
          </div>
        </div>

        {/* 同名课程提示：说明保存会走「合并」而不是新建 */}
        {existing && (
          <div className="flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] leading-5 text-amber-700">
            <Info size={14} className="mt-0.5 shrink-0" />
            <span>
              已有同名课程「{existing.name}
              {existing.teacher ? ` · ${existing.teacher}` : ""}」（当前 {existing.slots?.length ?? 0}{" "}
              个时段）：保存会把下面的时段合并进去，不重复建课。
            </span>
          </div>
        )}

        {/* 上课时段：填了才会出现在周课表上 */}
        <div>
          <div className="mb-1.5 flex items-center justify-between">
            <span className="text-xs font-semibold text-slate-700">
              上课时段（{slots.length}）
            </span>
            <button
              type="button"
              onClick={() => setSlots((prev) => [...prev, newSlot((prev.length % 7) + 1)])}
              className="flex items-center gap-1 rounded-lg border border-slate-200 px-2 py-1 text-[11px] text-slate-600 transition hover:border-brand-300 hover:text-brand-600"
            >
              <Plus size={12} /> 添加时段
            </button>
          </div>
          {slots.length === 0 && (
            <p className="rounded-xl bg-slate-50 px-3 py-2 text-[11px] text-slate-400">
              没有时段：课程会保存，但在周课表上暂时不显示（可在「设置 → 我的课程」或这里补上）
            </p>
          )}
          <div className="space-y-2">
            {slots.map((s, i) => (
              <div
                key={i}
                className="rounded-xl border border-slate-200 bg-slate-50/60 px-2.5 py-2"
              >
                <div className="flex flex-wrap items-center gap-1.5">
                  <select
                    value={s.weekday}
                    aria-label="星期"
                    onChange={(e) => setSlot(i, { weekday: Number(e.target.value) })}
                    className="rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-xs text-slate-700 outline-none focus:border-brand-500"
                  >
                    {WEEKDAY_NAMES.map((d, di) => (
                      <option key={d} value={di + 1}>
                        {d}
                      </option>
                    ))}
                  </select>
                  <input
                    type="time"
                    value={s.start}
                    aria-label="开始时间"
                    onChange={(e) => setSlot(i, { start: e.target.value })}
                    className="rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-xs tabular-nums text-slate-700 outline-none focus:border-brand-500"
                  />
                  <span className="text-xs text-slate-400">–</span>
                  <input
                    type="time"
                    value={s.end}
                    aria-label="结束时间"
                    onChange={(e) => setSlot(i, { end: e.target.value })}
                    className="rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-xs tabular-nums text-slate-700 outline-none focus:border-brand-500"
                  />
                  <input
                    value={s.weeks}
                    aria-label="上课周次"
                    list="course-editor-weeks"
                    onChange={(e) => setSlot(i, { weeks: e.target.value })}
                    placeholder="周次：1-16 / 1-16单"
                    className="w-32 rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-xs text-slate-700 outline-none focus:border-brand-500"
                  />
                  <input
                    value={s.location}
                    aria-label="该时段地点"
                    onChange={(e) => setSlot(i, { location: e.target.value })}
                    placeholder="地点（可选）"
                    className="w-28 rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-xs text-slate-700 outline-none focus:border-brand-500"
                  />
                  <button
                    type="button"
                    onClick={() => setSlots((prev) => prev.filter((_, si) => si !== i))}
                    aria-label={`删除第 ${i + 1} 个时段`}
                    className="ml-auto rounded-lg p-1.5 text-slate-400 transition hover:bg-red-50 hover:text-red-500"
                  >
                    <Trash2 size={13} />
                  </button>
                </div>
                {/* 解析回显：周次写法是否读得懂，当场看得见 */}
                <div className={`mt-1 pl-0.5 text-[10px] ${parsed[i].err ? "text-red-500" : "text-slate-400"}`}>
                  {parsed[i].err ? `⚠ ${parsed[i].err}` : `周次：${weeksHint(parsed[i].weeks)}`}
                </div>
              </div>
            ))}
          </div>
          <datalist id="course-editor-weeks">
            <option value="1-16" />
            <option value="1-16单" />
            <option value="1-16双" />
            <option value="1-8" />
            <option value="1-18" />
            <option value="每周" />
          </datalist>
        </div>

        {error && <p className="text-xs text-red-500">{error}</p>}

        <div className="flex items-center justify-between gap-2 border-t border-slate-100 pt-3">
          <span className="flex items-center gap-1.5 text-[11px] text-slate-400">
            <CalendarPlus size={13} className="shrink-0 text-brand-500" />
            {existing ? "保存 = 合并到同名课程" : "保存后可在周课表上直接看到"}
          </span>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={onClose}
              className="rounded-xl border border-slate-200 px-3 py-2 text-xs text-slate-500 hover:text-slate-700"
            >
              取消
            </button>
            <button
              type="submit"
              disabled={busy || !name.trim() || !!slotError}
              className="flex items-center gap-1.5 rounded-xl bg-brand-600 px-5 py-2 text-sm font-medium text-white transition hover:bg-brand-700 disabled:opacity-50"
            >
              {busy ? <Spinner /> : <Check size={15} />}
              {existing ? "合并保存" : "保存课程"}
            </button>
          </div>
        </div>
      </form>
    </Modal>
  );
}
