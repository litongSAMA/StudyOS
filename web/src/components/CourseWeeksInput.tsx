import { useId, useState } from "react";
import { MAX_WEEKS, parseWeeks } from "../lib/courses";

const PRESETS = [
  { label: "1–16周", value: "1-16" },
  { label: "1–18周", value: "1-18" },
  { label: "单周（1–16）", value: "1-16单" },
  { label: "双周（1–16）", value: "1-16双" },
  { label: "每周", value: "每周" },
];

/** 文本输入与逐周点选共用同一个草稿，保存时仍使用课程编辑器的周次校验。 */
export default function CourseWeeksInput({ value, onChange }: {
  value: string;
  onChange: (value: string) => void;
}) {
  const id = useId();
  const [expanded, setExpanded] = useState(false);
  const [showAll, setShowAll] = useState(false);
  let weeks: number[] = [];
  let valid = Boolean(value.trim());
  try {
    weeks = parseWeeks(value);
  } catch {
    valid = false;
  }
  const everyWeek = valid && weeks.length === 0;
  const visibleCount = showAll ? MAX_WEEKS : Math.max(20, ...weeks);

  const toggleWeek = (week: number) => {
    const selected = new Set(weeks);
    if (selected.has(week)) selected.delete(week);
    else selected.add(week);
    onChange([...selected].sort((a, b) => a - b).join(","));
  };

  return (
    <div className="mt-2 border-t border-slate-200/70 pt-2">
      <div className="flex flex-wrap items-center gap-2">
        <label htmlFor={id} className="text-xs font-medium text-slate-600">上课周次</label>
        <input
          id={id}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          aria-invalid={!valid}
          aria-describedby={`${id}-help`}
          placeholder="如：1,3,5-8,12"
          className="min-w-0 flex-1 rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-xs text-slate-700 outline-none focus:border-brand-500"
        />
        <button
          type="button"
          aria-expanded={expanded}
          aria-controls={`${id}-picker`}
          onClick={() => setExpanded((open) => !open)}
          className={`rounded-lg border px-2.5 py-1.5 text-xs transition ${expanded ? "border-brand-300 bg-brand-50 text-brand-700" : "border-slate-200 bg-white text-slate-600 hover:border-brand-300 hover:text-brand-600"}`}
        >
          {expanded ? "收起周次" : "自定义周次"}
        </button>
      </div>
      <div className="mt-2 flex flex-wrap gap-1.5" role="group" aria-label="常用周次">
        {PRESETS.map((preset) => {
          const presetWeeks = parseWeeks(preset.value);
          const selected = valid && weeks.join(",") === presetWeeks.join(",");
          return (
            <button
              key={preset.value}
              type="button"
              aria-pressed={selected}
              onClick={() => onChange(preset.value)}
              className={`rounded-md border px-2 py-1 text-[11px] transition ${selected ? "border-brand-300 bg-brand-50 text-brand-700" : "border-slate-200 bg-white text-slate-500 hover:border-brand-300 hover:text-brand-600"}`}
            >
              {preset.label}
            </button>
          );
        })}
      </div>
      <p id={`${id}-help`} className="mt-1.5 text-[10px] leading-4 text-slate-400">
        支持单周、连续范围与混合周次，如 1,3,5-8,12；每个时段可单独设置（1–{MAX_WEEKS}周）。
      </p>
      {expanded && (
        <div id={`${id}-picker`} className="mt-2 rounded-lg border border-slate-200 bg-white p-2.5">
          <div className="mb-2 flex flex-wrap items-center justify-between gap-2 text-[11px]">
            <span className="text-slate-500">
              {everyWeek ? "每周上课；点击数字可改为指定周次" : `点击选择或取消 · 已选 ${weeks.length} 周`}
            </span>
            <button type="button" onClick={() => onChange("")} className="text-slate-400 hover:text-red-500">
              清空选择
            </button>
          </div>
          <div className="grid grid-cols-5 gap-1.5 sm:grid-cols-10" role="group" aria-label="自定义上课周次">
            {Array.from({ length: visibleCount }, (_, i) => i + 1).map((week) => (
              <button
                key={week}
                type="button"
                aria-label={`第 ${week} 周`}
                aria-pressed={weeks.includes(week)}
                onClick={() => toggleWeek(week)}
                className={`rounded-md border py-1.5 text-xs tabular-nums transition ${weeks.includes(week) ? "border-brand-500 bg-brand-600 text-white" : "border-slate-200 text-slate-500 hover:border-brand-300 hover:bg-brand-50"}`}
              >
                {week}
              </button>
            ))}
          </div>
          {visibleCount < MAX_WEEKS && (
            <button type="button" onClick={() => setShowAll(true)} className="mt-2 text-[11px] text-brand-600 hover:text-brand-700">
              展开至第 {MAX_WEEKS} 周
            </button>
          )}
        </div>
      )}
    </div>
  );
}
