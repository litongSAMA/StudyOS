/**
 * 「查看支持的格式」面板：按 group 分区列出格式徽标。
 *
 * - 只展示接口/兜底目录里的数据，`supported=false` 的格式灰显，`title` 显示 note；
 * - 默认折叠（上传区本来就窄），展开后 2 列排布，移动端传 `compact` 更紧凑；
 * - 纯展示组件：不请求、不写状态（展开状态除外），样式随 web/mobile 复用。
 */
import { useState } from "react";
import { ChevronDown, ScanText } from "lucide-react";
import type { FormatGroup, FormatInfo } from "../types";

function badgeClass(format: FormatInfo, compact: boolean): string {
  const base = compact ? "px-1.5 py-0.5 text-[10px]" : "px-2 py-0.5 text-[11px]";
  return format.supported
    ? `${base} rounded-md bg-slate-100 text-slate-600 ring-1 ring-slate-200`
    : `${base} rounded-md bg-slate-50 text-slate-400 ring-1 ring-slate-100`;
}

function titleOf(format: FormatInfo): string {
  const parts = [format.label];
  if (format.ocr) parts.push("走 OCR 识别");
  if (!format.supported) parts.push("本机暂不支持解析");
  if (format.note) parts.push(format.note);
  return parts.join(" · ");
}

export default function FormatCatalog({
  groups,
  unknownNote,
  ocrAvailable,
  degraded = false,
  compact = false,
}: {
  groups: FormatGroup[];
  /** 未知类型提示（接口可能没给，调用方用 unknownNoteOf 兜底） */
  unknownNote: string;
  /** 本机 OCR 是否可用 */
  ocrAvailable?: boolean;
  /** 目录接口不可用、当前展示的是兜底列表 */
  degraded?: boolean;
  /** 移动端紧凑排版 */
  compact?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const safeGroups = (groups ?? []).filter((group) => (group?.formats?.length ?? 0) > 0);

  return (
    <div className={compact ? "text-[11px]" : "text-xs"}>
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        className="flex items-center gap-1 text-slate-500 underline decoration-dotted underline-offset-2 hover:text-slate-700"
      >
        <ChevronDown size={compact ? 12 : 13} className={`transition-transform ${open ? "rotate-180" : ""}`} />
        {open ? "收起格式列表" : "查看支持的格式"}
      </button>
      {open && (
        <div className="mt-2 space-y-2.5 rounded-xl border border-slate-100 bg-slate-50/60 p-3">
          {degraded && (
            <p className="text-[10px] leading-4 text-amber-600">
              格式目录接口暂不可用，当前展示常用格式，上传功能不受影响
            </p>
          )}
          {safeGroups.length === 0 ? (
            <p className="text-[10px] text-slate-400">暂无格式列表，仍可直接上传文件由后端识别</p>
          ) : (
            <div className={compact ? "space-y-2.5" : "grid gap-x-4 gap-y-3 sm:grid-cols-2"}>
              {safeGroups.map((group) => (
                <section key={group.id} className="min-w-0">
                  <h3 className="flex items-center gap-1.5 font-semibold text-slate-500">
                    {group.label || group.id}
                    <span className="font-normal text-slate-300">{group.formats.length}</span>
                  </h3>
                  <div className="mt-1.5 flex flex-wrap gap-1">
                    {group.formats.map((format) => (
                      <span key={`${group.id}-${format.ext}`} title={titleOf(format)} className={badgeClass(format, compact)}>
                        <span className="font-medium">{format.ext}</span>
                        {!compact && <span className="ml-1 text-slate-400">{format.label}</span>}
                      </span>
                    ))}
                  </div>
                </section>
              ))}
            </div>
          )}
          <p className="flex items-start gap-1 text-[10px] leading-4 text-slate-400">
            <ScanText size={11} className="mt-0.5 shrink-0" />
            <span>
              {ocrAvailable === false ? "本机未启用离线 OCR，" : "图片 / 扫描件走离线 OCR；"}
              灰色格式本机暂不能抽取（鼠标悬停可看原因）。{unknownNote}
            </span>
          </p>
        </div>
      )}
    </div>
  );
}
