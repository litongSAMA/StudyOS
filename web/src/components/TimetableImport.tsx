import { useEffect, useMemo, useRef, useState } from "react";
import {
  CalendarPlus,
  Check,
  ClipboardPaste,
  FileType2,
  FileUp,
  Image as ImageIcon,
  Info,
  Sparkles,
  X,
} from "lucide-react";
import { apiCourses, ApiHttpError } from "../api";
import { importFormatLabel, TIMETABLE_ACCEPT } from "../lib/formats";
import type { CourseImportEntry, CourseImportPreview, CourseSlot } from "../types";
import { Modal, Spinner } from "./ui";

export const DAY_NAMES = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"];

const MAX_AUTO_RETRY = 3;
const RETRY_DELAY_MS = 3000;
/** 教务系统 Excel/CSV/PDF/网页/Word 课表 + iCal/JSON + 课表截图（含 tif/gif） */
const ACCEPT = TIMETABLE_ACCEPT;
const IMG_EXTS = [".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"];
/** 上传区与解析中的统一提示文案 */
const SUPPORT_HINT = "支持 教务系统 Excel/CSV/PDF/网页/Word 课表、iCal、JSON 与课表截图（OCR + AI 兜底）";

const GRID_START = 8 * 60; // 课表预览从 08:00 开始
const GRID_END = 22 * 60 + 30; // 到 22:30
const STEP = 30; // 半小时一格
const ROW_H = 24;

function timeToMin(t: string): number {
  const [h, m] = t.split(":").map(Number);
  return (h || 0) * 60 + (m || 0);
}

/** 周次列表 → 「第1-16周 / 第1,3,5周」压缩写法 */
function compressWeeks(weeks: number[]): string {
  if (!weeks.length) return "周次未注明";
  const parts: string[] = [];
  let i = 0;
  while (i < weeks.length) {
    let j = i;
    while (j + 1 < weeks.length && weeks[j + 1] === weeks[j] + 1) j++;
    parts.push(j > i ? `${weeks[i]}-${weeks[j]}` : `${weeks[i]}`);
    i = j + 1;
  }
  return `第${parts.join(",")}周`;
}

function slotLabel(slot: CourseSlot): string {
  return (
    `${DAY_NAMES[slot.weekday - 1]} ${slot.start}-${slot.end} · ` +
    `${compressWeeks(slot.weeks)}${slot.location ? ` · ${slot.location}` : ""}`
  );
}

/** 选中条目全部时段的时间跨度（预览网格边界，含未选中的兜底） */
function gridBounds(entries: CourseImportEntry[]): { start: number; end: number } {
  let start = GRID_START;
  let end = GRID_END;
  for (const e of entries) {
    for (const s of e.slots) {
      start = Math.min(start, Math.floor(timeToMin(s.start) / STEP) * STEP);
      end = Math.max(end, Math.ceil(timeToMin(s.end) / STEP) * STEP);
    }
  }
  return { start, end };
}

export default function TimetableImport({
  onClose,
  onImported,
}: {
  onClose: () => void;
  onImported: () => void;
}) {
  const [text, setText] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [previewUrl, setPreviewUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [retryHint, setRetryHint] = useState("");
  const [preview, setPreview] = useState<CourseImportPreview | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [semester, setSemester] = useState("");
  const [done, setDone] = useState<{ created: number; updated: number } | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const retryTimer = useRef<number | null>(null);

  // 卸载时清理重试定时器与图片预览 URL
  useEffect(() => {
    return () => {
      if (retryTimer.current) window.clearTimeout(retryTimer.current);
    };
  }, []);

  const isImage = (f: File | null) =>
    !!f && IMG_EXTS.some((ext) => f.name.toLowerCase().endsWith(ext));

  const pickFile = (f: File | null) => {
    if (retryTimer.current) window.clearTimeout(retryTimer.current);
    setRetryHint("");
    setError("");
    setFile(f);
    setPreviewUrl((prev) => {
      if (prev) URL.revokeObjectURL(prev);
      return f && isImage(f) ? URL.createObjectURL(f) : "";
    });
  };

  const parse = async (retries = 0) => {
    if (!file && !text.trim()) {
      setError("请先上传课表文件（.ics/.xlsx/.csv/.pdf/网页/Word/图片）或粘贴课表内容");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const pv = await apiCourses.importPreview(file ? { file } : { text });
      setPreview(pv);
      setSelected(new Set(pv.entries.map((e) => e.name)));
      setSemester(pv.semester || "");
      setDone(null);
      setRetryHint("");
      setBusy(false);
    } catch (e) {
      // OCR 模型首次加载中：后端返回 503，自动延时重试，用户无需手动点
      if (e instanceof ApiHttpError && e.status === 503 && retries < MAX_AUTO_RETRY) {
        const next = retries + 1;
        setRetryHint(
          `OCR 模型首次加载中，${RETRY_DELAY_MS / 1000} 秒后自动重试（第 ${next}/${MAX_AUTO_RETRY} 次）…`,
        );
        retryTimer.current = window.setTimeout(() => parse(next), RETRY_DELAY_MS);
        return; // 保持 busy，不结束加载态
      }
      setRetryHint("");
      setError((e as Error).message);
      setBusy(false);
    }
  };

  const apply = async () => {
    if (!preview) return;
    const entries = preview.entries.filter((e) => selected.has(e.name));
    if (!entries.length) {
      setError("请至少勾选一门课程");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const res = await apiCourses.importApply(entries, {
        semester: semester.trim(),
        start_date: preview.term_start_date,
      });
      setDone({ created: res.created.length, updated: res.updated.length });
      onImported();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const toggle = (name: string) => {
    const next = new Set(selected);
    if (next.has(name)) next.delete(name);
    else next.add(name);
    setSelected(next);
  };

  const shown = useMemo(
    () => (preview ? preview.entries.filter((e) => selected.has(e.name)) : []),
    [preview, selected],
  );
  const bounds = useMemo(() => gridBounds(shown), [shown]);
  const rowCount = (bounds.end - bounds.start) / STEP;

  const hourLines: number[] = [];
  for (let t = Math.ceil(bounds.start / 60) * 60; t <= bounds.end; t += 60) hourLines.push(t);

  return (
    <Modal title="导入课表" onClose={onClose} wide>
      {done ? (
        <div className="py-8 text-center">
          <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-full bg-emerald-100 text-emerald-600">
            <Check size={24} />
          </div>
          <p className="text-base font-semibold text-slate-800">课表导入完成 🎉</p>
          <p className="mt-1 text-sm text-slate-500">
            新增 {done.created} 门课程、更新 {done.updated} 门课程（课表时段/老师/地点已合并）
          </p>
          <p className="mt-1 text-xs text-slate-400">
            课程已带上课表信息；「我的课程」里可继续手动增删
          </p>
          <button
            onClick={onClose}
            className="mt-5 rounded-xl bg-brand-600 px-6 py-2 text-sm font-medium text-white hover:bg-brand-700"
          >
            完成
          </button>
        </div>
      ) : !preview ? (
        <div className="space-y-3">
          <p className="text-xs leading-5 text-slate-500">
            {SUPPORT_HINT}，解析后先预览再确认导入（同名课程自动合并，不会重复建课）：
          </p>
          <div className="grid gap-3 md:grid-cols-2">
            {/* 文件上传 */}
            <div
              className="flex cursor-pointer flex-col items-center justify-center rounded-2xl border-2 border-dashed border-slate-200 bg-slate-50/60 px-4 py-6 text-center transition hover:border-brand-300 hover:bg-brand-50/40"
              onClick={() => fileRef.current?.click()}
              onDragOver={(e) => e.preventDefault()}
              onDrop={(e) => {
                e.preventDefault();
                const f = e.dataTransfer.files?.[0];
                if (f) pickFile(f);
              }}
            >
              {file ? (
                <div className="flex w-full items-center gap-3 text-left" onClick={(e) => e.stopPropagation()}>
                  {previewUrl ? (
                    <img src={previewUrl} alt="课表预览" className="h-16 w-16 shrink-0 rounded-lg border border-slate-200 object-cover" />
                  ) : (
                    <div className="flex h-16 w-16 shrink-0 items-center justify-center rounded-lg bg-brand-100 text-brand-600">
                      <FileUp size={22} />
                    </div>
                  )}
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-medium text-slate-700">{file.name}</div>
                    <div className="mt-0.5 text-[11px] text-slate-400">
                      {(file.size / 1024).toFixed(0)} KB · 点击更换文件
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() => pickFile(null)}
                    className="shrink-0 rounded-md p-1 text-slate-400 hover:bg-slate-200 hover:text-slate-600"
                    title="移除文件"
                  >
                    <X size={15} />
                  </button>
                </div>
              ) : (
                <>
                  <FileUp size={22} className="mb-2 text-brand-500" />
                  <div className="text-sm font-medium text-slate-700">拖入 / 点击选择课表文件</div>
                  <div className="mt-1 text-[11px] leading-4 text-slate-400">
                    Excel(.xlsx/.xls/.ods)、CSV、PDF、网页(.html)、Word、iCal(.ics)、JSON、课表截图
                  </div>
                </>
              )}
              <input
                ref={fileRef}
                type="file"
                accept={ACCEPT}
                className="hidden"
                onChange={(e) => pickFile(e.target.files?.[0] ?? null)}
              />
            </div>
            {/* 文本粘贴 */}
            <div className="flex flex-col">
              <div className="mb-1.5 flex items-center gap-1.5 text-[11px] text-slate-400">
                <ClipboardPaste size={13} />
                或粘贴课表文本（iCal 内容 / JSON 课表）
              </div>
              <textarea
                rows={7}
                value={text}
                onChange={(e) => setText(e.target.value)}
                placeholder={'BEGIN:VCALENDAR\n…\n或 {"courses":[{"name":"操作系统","slots":[{"weekday":1,"start":"08:00","end":"09:40","weeks":"1-16"}]}]}'}
                className="flex-1 resize-none rounded-xl border border-slate-200 px-3 py-2.5 font-mono text-[11px] leading-5 outline-none transition focus:border-brand-500"
              />
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-1.5 text-[10px] text-slate-400">
            <span className="rounded-md bg-slate-100 px-1.5 py-0.5">.ics 教务导出</span>
            <span className="rounded-md bg-slate-100 px-1.5 py-0.5">.xlsx/.xls/.ods 二维课表</span>
            <span className="rounded-md bg-slate-100 px-1.5 py-0.5">.csv/.tsv</span>
            <span className="rounded-md bg-slate-100 px-1.5 py-0.5">.pdf / .html 网页课表</span>
            <span className="rounded-md bg-slate-100 px-1.5 py-0.5">.docx Word 课表</span>
            <span className="rounded-md bg-slate-100 px-1.5 py-0.5">.json</span>
            <span className="inline-flex items-center gap-0.5 rounded-md bg-slate-100 px-1.5 py-0.5">
              <ImageIcon size={11} /> 课表截图/拍照
            </span>
          </div>
          <div className="flex items-center gap-2 rounded-xl bg-slate-50 px-3 py-2 text-[11px] leading-5 text-slate-500">
            <Info size={14} className="shrink-0 text-brand-500" />
            <span>
              解析时会自动识别课程名、任课老师、上课地点、星期/节次与周次（含单双周）；
              Excel/PDF/图片课表按节次换算时间，图片课表走离线 OCR + AI 兜底，导入后可在预览里勾选与微调
            </span>
          </div>
          <div className="flex items-center justify-between gap-2">
            <button
              onClick={() => {
                pickFile(null);
                if (fileRef.current) fileRef.current.value = "";
                setText("");
                setError("");
                setRetryHint("");
              }}
              className="text-xs text-slate-400 hover:text-slate-600"
            >
              清空重来
            </button>
            <button
              onClick={() => parse(0)}
              disabled={busy || (!file && !text.trim())}
              className="flex items-center gap-1.5 rounded-xl bg-brand-600 px-5 py-2 text-sm font-medium text-white transition hover:bg-brand-700 disabled:opacity-50"
            >
              {busy ? <Spinner /> : <Sparkles size={15} />}
              {busy && retryHint ? "等待模型加载…" : "解析预览"}
            </button>
          </div>
          {retryHint && (
            <div className="flex items-center gap-2 rounded-xl border border-blue-200 bg-blue-50 px-3 py-2 text-xs text-blue-700">
              <Spinner />
              {retryHint}
            </div>
          )}
          {error && <p className="text-xs text-red-500">{error}</p>}
        </div>
      ) : (
        <div className="space-y-4">
          {/* 来源格式（format_label 缺失时退回 format 原值） */}
          <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
            <FileType2 size={13} className="shrink-0 text-brand-500" />
            <span>来源格式</span>
            <span className="rounded-lg bg-slate-100 px-2 py-0.5 text-[11px] font-medium text-slate-600 ring-1 ring-slate-200">
              {importFormatLabel(preview)}
            </span>
            <span className="text-[11px] text-slate-400">
              已识别 {preview.entries.length} 门课程，勾选后导入
            </span>
          </div>
          {/* AI 智能兜底识别提示（仅图片且走 LLM 通道时显示） */}
          {preview.ai_assisted && preview.format === "image" && (
            <div className="flex items-center gap-2 rounded-xl border border-violet-200 bg-violet-50 px-3 py-2 text-xs text-violet-700">
              <Sparkles size={14} className="shrink-0" />
              <span>
                已通过 <b>AI 智能识别</b> 解析课表图片。请核对下方识别结果，课程名/节次/周次如有偏差可在导入前手动调整
              </span>
            </div>
          )}
          {/* 周课表预览网格 */}
          {shown.length > 0 && (
            <div className="overflow-x-auto">
              <div className="min-w-[560px]">
                <div
                  className="grid"
                  style={{ gridTemplateColumns: `44px repeat(7, 1fr)` }}
                >
                  <div />
                  {DAY_NAMES.map((d) => (
                    <div key={d} className="border-b border-slate-100 pb-1 text-center text-xs font-medium text-slate-500">
                      {d}
                    </div>
                  ))}
                </div>
                <div
                  className="grid"
                  style={{ gridTemplateColumns: `44px repeat(7, 1fr)` }}
                >
                  {/* 时间轴 */}
                  <div className="relative" style={{ height: rowCount * ROW_H }}>
                    {hourLines.map((t) => (
                      <span
                        key={t}
                        className="absolute right-1.5 -translate-y-1/2 text-[10px] text-slate-400"
                        style={{ top: ((t - bounds.start) / STEP) * ROW_H }}
                      >
                        {String(Math.floor(t / 60)).padStart(2, "0")}:00
                      </span>
                    ))}
                  </div>
                  {/* 每天一列，课程块绝对定位 */}
                  {DAY_NAMES.map((_, dayIdx) => (
                    <div
                      key={dayIdx}
                      className="relative border-l border-slate-100"
                      style={{ height: rowCount * ROW_H }}
                    >
                      {hourLines.map((t) => (
                        <div
                          key={t}
                          className="absolute inset-x-0 border-t border-slate-100"
                          style={{ top: ((t - bounds.start) / STEP) * ROW_H }}
                        />
                      ))}
                      {shown.flatMap((e) =>
                        e.slots
                          .filter((s) => s.weekday === dayIdx + 1)
                          .map((s, si) => {
                            const top = ((timeToMin(s.start) - bounds.start) / STEP) * ROW_H;
                            const height = Math.max(
                              ((timeToMin(s.end) - timeToMin(s.start)) / STEP) * ROW_H - 1,
                              18,
                            );
                            return (
                              <div
                                key={`${e.name}-${si}`}
                                className="absolute inset-x-0.5 overflow-hidden rounded-md px-1 py-0.5 text-white"
                                style={{
                                  top,
                                  height,
                                  backgroundColor: e.color,
                                }}
                                title={`${e.name} ${s.start}-${s.end} · ${compressWeeks(s.weeks)}`}
                              >
                                <div className="truncate text-[10px] font-medium leading-tight">{e.name}</div>
                                <div className="truncate text-[9px] leading-tight opacity-80">
                                  {s.start}-{s.end}
                                </div>
                              </div>
                            );
                          }),
                      )}
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}

          {/* 课程条目勾选 */}
          <div className="max-h-64 space-y-2 overflow-y-auto pr-1">
            {preview.entries.map((e) => {
              const checked = selected.has(e.name);
              return (
                <label
                  key={e.name}
                  className="flex cursor-pointer items-start gap-2.5 rounded-xl border border-slate-100 px-3 py-2 transition hover:border-brand-200"
                >
                  <input
                    type="checkbox"
                    className="mt-1 accent-brand-600"
                    checked={checked}
                    onChange={() => toggle(e.name)}
                  />
                  <span className="h-3 w-3 shrink-0 rounded-full" style={{ backgroundColor: e.color, marginTop: 3 }} />
                  <span className="min-w-0 flex-1">
                    <span className="flex flex-wrap items-center gap-1.5 text-sm text-slate-800">
                      {e.name}
                      {e.teacher && <span className="text-[11px] text-slate-400">{e.teacher}</span>}
                      {e.exists ? (
                        <span className="rounded-md bg-amber-50 px-1.5 py-0.5 text-[10px] font-medium text-amber-600 ring-1 ring-amber-200">
                          已存在 · 将合并
                        </span>
                      ) : (
                        <span className="rounded-md bg-emerald-50 px-1.5 py-0.5 text-[10px] font-medium text-emerald-600 ring-1 ring-emerald-200">
                          新课程
                        </span>
                      )}
                    </span>
                    <span className="mt-0.5 flex flex-wrap gap-1">
                      {e.slots.map((s, i) => (
                        <span
                          key={i}
                          className="rounded bg-slate-50 px-1.5 py-0.5 text-[10px] text-slate-500 ring-1 ring-slate-100"
                        >
                          {slotLabel(s)}
                        </span>
                      ))}
                    </span>
                  </span>
                </label>
              );
            })}
          </div>

          {/* 学期与导入操作 */}
          <div className="flex flex-wrap items-center justify-between gap-2 border-t border-slate-100 pt-3">
            <div className="flex items-center gap-2 text-xs text-slate-500">
              学期
              <input
                value={semester}
                onChange={(e) => setSemester(e.target.value)}
                placeholder="如 2025-2026-1"
                className="w-32 rounded-lg border border-slate-200 px-2 py-1 text-xs outline-none focus:border-brand-500"
              />
              {preview.term_start_date && (
                <span className="text-slate-400">
                  开学日 {preview.term_start_date}（用于「第N周」换算）
                </span>
              )}
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={() => {
                  setPreview(null);
                  setDone(null);
                }}
                className="rounded-xl border border-slate-200 px-3 py-2 text-xs text-slate-500 hover:text-slate-700"
              >
                <span className="inline-flex items-center gap-1">
                  <CalendarPlus size={13} /> 重新选择
                </span>
              </button>
              <button
                onClick={apply}
                disabled={busy || !selected.size}
                className="flex items-center gap-1.5 rounded-xl bg-brand-600 px-5 py-2 text-sm font-medium text-white transition hover:bg-brand-700 disabled:opacity-50"
              >
                {busy ? <Spinner /> : <Check size={15} />}
                确认导入（{selected.size}）
              </button>
            </div>
          </div>
          {error && <p className="text-xs text-red-500">{error}</p>}
        </div>
      )}
    </Modal>
  );
}
