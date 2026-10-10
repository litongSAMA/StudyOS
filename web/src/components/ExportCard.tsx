import { useEffect, useState } from "react";
import {
  BookOpen,
  CalendarClock,
  Download,
  History,
  ListChecks,
  RefreshCw,
  Trash2,
} from "lucide-react";
import { apiExport, exportUrl } from "../api";
import type { ExportFmt, ExportKind } from "../api";
import type { ExportKindStatus, ExportRecord, ExportStatus } from "../types";
import DownloadLink from "./DownloadLink";
import { Spinner } from "./ui";
import { fmtDT } from "../utils";

const KIND_ICON: Record<string, typeof CalendarClock> = {
  tasks: CalendarClock,
  materials: BookOpen,
  review: ListChecks,
};

const FMT_LABEL: Record<string, string> = {
  ical: "iCal（导入系统日历）",
  csv: "CSV（Excel）",
  md: "Markdown",
};

const FMT_SHORT: Record<string, string> = { ical: "iCal", csv: "CSV", md: "MD" };

/** 兜底文件名（服务端会下发 Content-Disposition，正常用不到） */
const EXT: Record<string, string> = { ical: ".ics", csv: ".csv", md: ".md" };

/** 三种类型 × 三种格式全矩阵（资料目录 iCal 为真 iCal：入库时间线全天事件） */
const FORMATS: Record<string, ExportFmt[]> = {
  tasks: ["ical", "csv", "md"],
  materials: ["ical", "csv", "md"],
  review: ["ical", "csv", "md"],
};

/** 各格式语义说明（悬浮提示） */
const FMT_HINT: Record<string, Partial<Record<ExportFmt, string>>> = {
  tasks: { ical: "DDL 任务 → VTODO（VALARM 提前提醒）" },
  materials: { ical: "资料入库时间线：每条资料一个全天事件（锚定入库日期）" },
  review: { ical: "复习计划 → VEVENT 每日复习块" },
};

export default function ExportCard() {
  const [status, setStatus] = useState<ExportStatus | null>(null);
  const [incremental, setIncremental] = useState(false);
  const [busy, setBusy] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const [msg, setMsg] = useState("");

  const load = async () => {
    try {
      setStatus(await apiExport.status());
    } catch (e) {
      setMsg(`读取导出状态失败：${(e as Error).message}`);
    }
  };

  useEffect(() => {
    load();
  }, []);

  /** 下载走 fetch + Blob（见 DownloadLink）：导出记录在服务端已写入 → 稍后刷新状态 */
  const afterDownload = (name: string) => {
    setMsg(`已开始下载 ${name}（导出记录已写入历史，可作下次增量基线）`);
    setTimeout(load, 600);
  };

  const clearHistory = async () => {
    if (!confirm("清空导出历史会同时重置增量基线：下一次「仅导出变更」将退化为全量导出。继续？")) return;
    setBusy(true);
    try {
      const r = await apiExport.clearRecords();
      setMsg(`已清空 ${r.deleted} 条导出记录，增量基线已重置`);
      await load();
    } finally {
      setBusy(false);
    }
  };

  const records: ExportRecord[] = status?.records ?? [];

  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="mb-1 flex flex-wrap items-center gap-2 text-sm font-semibold text-slate-700">
        <Download size={16} className="text-brand-500" />
        数据导出（导出即备份，数据永远可带走）
        <label className="ml-auto flex cursor-pointer items-center gap-1.5 text-xs font-normal text-slate-600">
          <input
            type="checkbox"
            checked={incremental}
            onChange={(e) => setIncremental(e.target.checked)}
          />
          仅导出自上次以来的变更（增量）
        </label>
        <button onClick={load} className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100" title="刷新导出状态">
          <RefreshCw size={14} />
        </button>
      </div>
      <p className="mb-4 text-[11px] text-slate-400">
        {incremental
          ? "增量导出以「同类型同格式的上次导出时间」为起点，只导出这之后新增或修改过的条目；首次导出没有基线时自动退化为全量。"
          : "全量导出当前所有条目。勾选上方「增量」后，每个格式按各自的上次导出时间只导出变更部分（适合定期同步到日历/Excel）。"}
      </p>

      <div className="space-y-3">
        {(status?.kinds ?? []).map((k) => (
          <KindRow
            key={k.kind}
            kind={k}
            incremental={incremental}
            onDownload={afterDownload}
            onError={setMsg}
          />
        ))}
        {!status && (
          <div className="flex items-center gap-2 text-xs text-slate-400">
            <Spinner /> 读取导出状态…
          </div>
        )}
      </div>

      {/* 导出历史（导出记录表的读取端：既是流水也是增量基线） */}
      <div className="mt-4 border-t border-slate-100 pt-3">
        <div className="flex flex-wrap items-center gap-2">
          <button
            onClick={() => setShowHistory((v) => !v)}
            className="flex items-center gap-1.5 text-xs font-medium text-slate-500 hover:text-brand-600"
          >
            <History size={13} /> 导出历史（{records.length}）{showHistory ? "收起" : "展开"}
          </button>
          {records.length > 0 && (
            <button
              onClick={clearHistory}
              disabled={busy}
              className="flex items-center gap-1 text-[11px] text-slate-400 hover:text-red-500 disabled:opacity-50"
            >
              <Trash2 size={12} /> 清空历史（重置增量基线）
            </button>
          )}
        </div>
        {showHistory && (
          <div className="mt-2 space-y-1">
            {records.length === 0 && (
              <div className="text-[11px] text-slate-400">还没有导出记录——导出一次即可建立增量基线</div>
            )}
            {records.map((r) => (
              <div
                key={r.id}
                className="flex flex-wrap items-center gap-x-2 gap-y-0.5 rounded-lg bg-slate-50 px-2.5 py-1.5 text-[11px] text-slate-500"
              >
                <span className="font-medium text-slate-600">{fmtDT(r.created_at)}</span>
                <span className="rounded bg-white px-1.5 py-0.5 ring-1 ring-slate-200">
                  {r.kind} · {FMT_SHORT[r.format] ?? r.format}
                </span>
                <span>{r.item_count} 条</span>
                {r.incremental ? (
                  <span className="rounded bg-brand-50 px-1.5 py-0.5 text-brand-600">
                    增量{r.since_at ? ` · 起点 ${fmtDT(r.since_at)}` : "（首次=全量）"}
                  </span>
                ) : (
                  <span className="rounded bg-slate-100 px-1.5 py-0.5">全量</span>
                )}
                <span className="truncate text-slate-400">{r.file_path}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      {msg && <div className="mt-3 rounded-xl bg-slate-50 px-3 py-2 text-xs text-slate-600">{msg}</div>}
    </div>
  );
}

function KindRow({
  kind,
  incremental,
  onDownload,
  onError,
}: {
  kind: ExportKindStatus;
  incremental: boolean;
  onDownload: (filename: string) => void;
  onError: (message: string) => void;
}) {
  const Icon = KIND_ICON[kind.kind] ?? CalendarClock;
  const byFmt = Object.fromEntries(kind.formats.map((f) => [f.format, f]));
  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="flex w-44 items-center gap-2 text-sm text-slate-600">
        <Icon size={15} className="text-slate-400" /> {kind.label}
        <span className="text-[10px] text-slate-400">共 {kind.total}</span>
      </span>
      {FORMATS[kind.kind].map((fmt) => {
        const st = byFmt[fmt];
        const changed = st?.changed ?? kind.total;
        const nothingNew = incremental && changed === 0;
        const hint = FMT_HINT[kind.kind]?.[fmt];
        return (
          <DownloadLink
            key={fmt}
            path={exportUrl(kind.kind as ExportKind, fmt, { incremental })}
            fallbackName={`studyos-${kind.kind}${EXT[fmt] ?? ""}`}
            disabled={nothingNew}
            onDone={onDownload}
            onError={(message) => onError(`导出失败（${kind.label} · ${FMT_SHORT[fmt]}）：${message}`)}
            title={[
              hint,
              st?.last
                ? `上次导出 ${fmtDT(st.last.created_at)}（${st.last.item_count} 条）· 自那以来变更 ${changed} 条`
                : "还没有导出过这个格式",
            ]
              .filter(Boolean)
              .join("；")}
            className={`flex items-center gap-1 rounded-lg border px-3 py-1.5 text-xs transition ${
              nothingNew
                ? "cursor-not-allowed border-slate-100 text-slate-300"
                : "border-slate-200 text-slate-600 hover:border-brand-400 hover:text-brand-600"
            }`}
          >
            {FMT_LABEL[fmt]}
            {incremental && (
              <span
                className={`rounded px-1 text-[10px] ${
                  changed > 0 ? "bg-brand-50 text-brand-600" : "bg-slate-100 text-slate-400"
                }`}
              >
                {changed > 0 ? `+${changed}` : "无变更"}
              </span>
            )}
          </DownloadLink>
        );
      })}
      <span className="text-[10px] text-slate-400">
        {kind.last_export
          ? `上次导出 ${fmtDT(kind.last_export.created_at)}`
          : "尚未导出"}
      </span>
    </div>
  );
}
