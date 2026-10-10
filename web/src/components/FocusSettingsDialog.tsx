import { useState } from "react";
import { Modal } from "./ui";
import { DEFAULT_SETTINGS, DURATION_FIELDS, PRESETS } from "../focusSettings";
import type { FocusSettings } from "../focusSettings";

export default function FocusSettingsDialog({ settings, inProgress, onSave, onClose }: {
  settings: FocusSettings;
  inProgress: boolean;
  onSave: (settings: FocusSettings) => void;
  onClose: () => void;
}) {
  const [draft, setDraft] = useState(settings);
  const [values, setValues] = useState(() => Object.fromEntries(DURATION_FIELDS.map(([key]) => [key, String(settings[key])])));
  const [error, setError] = useState("");
  const reset = () => {
    setDraft({ ...DEFAULT_SETTINGS });
    setValues(Object.fromEntries(DURATION_FIELDS.map(([key]) => [key, String(DEFAULT_SETTINGS[key])])));
    setError("");
  };
  const save = async () => {
    const next = { ...draft };
    for (const [key, label, min, max] of DURATION_FIELDS) {
      const n = Number(values[key]);
      if (!values[key]?.trim() || !Number.isInteger(n) || n < min || n > max) {
        setError(`${label}请输入 ${min}–${max} 之间的整数`);
        return;
      }
      next[key] = n;
    }
    if (next.notify && (!('Notification' in window) || Notification.permission !== "granted")) {
      try {
        if (!('Notification' in window) || await Notification.requestPermission() !== "granted") {
          setError("通知权限未开启，请关闭通知选项后保存，或在浏览器中允许通知。");
          return;
        }
      } catch { setError("此浏览器暂不支持通知，请关闭通知选项后保存。"); return; }
    }
    onSave(next);
  };
  return (
    <Modal title="自定义番茄钟" onClose={onClose}>
      <form className="space-y-5" onSubmit={(event) => { event.preventDefault(); void save(); }}>
        <p className="text-sm leading-6 text-slate-500">
          {inProgress ? "当前计时继续保留，新时长从下一阶段或重置后生效。" : "按你的节奏安排专注与休息，保存后立即更新计时。"}
        </p>
        <fieldset>
          <legend className="mb-2 text-sm font-semibold text-slate-700">常用节奏</legend>
          <div className="flex flex-wrap gap-2">
            {PRESETS.map((preset) => (
              <button type="button" key={preset.label}
                aria-pressed={values.focus === String(preset.focus) && values.short === String(preset.short) && values.long === String(preset.long)}
                onClick={() => { setValues((previous) => ({ ...previous, focus: String(preset.focus), short: String(preset.short), long: String(preset.long) })); setError(""); }}
                className={`min-h-11 rounded-xl border px-3 text-xs font-medium ${values.focus === String(preset.focus) && values.short === String(preset.short) && values.long === String(preset.long) ? "border-brand-400 bg-brand-50 text-brand-700" : "border-slate-200 text-slate-600"}`}>
                {preset.label}
              </button>
            ))}
          </div>
        </fieldset>
        <div className="space-y-3">
          {DURATION_FIELDS.map(([key, label, min, max, unit]) => (
            <label key={key} className="flex items-center justify-between gap-3 text-sm text-slate-700">
              <span>{label}<span className="mt-0.5 block text-xs text-slate-400">{min}–{max} {unit}</span></span>
              <span className="flex shrink-0 items-center gap-2">
                <input type="number" inputMode="numeric" min={min} max={max} step={1} required aria-label={label}
                  value={values[key]} onChange={(event) => { setValues((previous) => ({ ...previous, [key]: event.target.value })); setError(""); }}
                  className="min-h-11 w-20 rounded-xl border border-slate-200 px-2 text-center outline-none focus:border-brand-500" />
                <span className="w-12 text-xs text-slate-500">{unit}</span>
              </span>
            </label>
          ))}
        </div>
        <div className="space-y-1 border-t border-slate-100 pt-3">
          {([
            ["autoStartBreak", "专注结束自动开始休息"],
            ["autoStartFocus", "休息结束自动开始专注"],
            ["sound", "阶段结束提示音"],
            ["notify", "专注结束浏览器通知"],
          ] as const).map(([key, label]) => (
            <label key={key} className="flex min-h-11 cursor-pointer items-center justify-between gap-3 text-sm text-slate-600">
              {label}<input type="checkbox" checked={draft[key]} onChange={(event) => setDraft((previous) => ({ ...previous, [key]: event.target.checked }))} className="h-5 w-5 shrink-0 accent-brand-600" />
            </label>
          ))}
        </div>
        {error && <p role="alert" className="text-sm text-red-600">{error}</p>}
        <p className="text-xs text-slate-400">设置会记住在当前浏览器中，下次打开自动使用。</p>
        <div className="sticky bottom-0 flex flex-wrap gap-2 bg-white py-2">
          <button type="button" onClick={reset} className="min-h-11 rounded-xl px-3 text-sm text-slate-500">恢复默认</button>
          <button type="button" onClick={onClose} className="min-h-11 rounded-xl border border-slate-200 px-4 text-sm text-slate-600">取消</button>
          <button type="submit" className="min-h-11 flex-1 rounded-xl bg-brand-600 px-4 text-sm font-medium text-white">保存设置</button>
        </div>
      </form>
    </Modal>
  );
}
