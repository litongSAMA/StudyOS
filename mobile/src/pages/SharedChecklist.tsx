import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import {
  Check,
  Clock,
  Download,
  GraduationCap,
  HardDriveDownload,
  Share2,
} from "lucide-react";
import { apiShare } from "../api";
import { Spinner } from "../components/ui";
import type { SharedChecklist } from "../types";
import { courseColor, dueLabel, fmtDT } from "../utils";

/** 分享页本地勾选进度（查看者自己的浏览器，不动分享者的数据） */
const progressKey = (token: string) => `studyos-share-progress-${token}`;

export default function SharedChecklistPage() {
  const { token = "" } = useParams();
  const [data, setData] = useState<SharedChecklist | null>(null);
  const [error, setError] = useState("");
  const [done, setDone] = useState<Record<number, boolean>>({});
  const [importHint, setImportHint] = useState("");

  useEffect(() => {
    let stale = false;
    apiShare
      .view(token)
      .then((d) => {
        if (stale) return;
        setData(d);
        try {
          setDone(JSON.parse(localStorage.getItem(progressKey(token)) ?? "{}"));
        } catch {
          setDone({});
        }
      })
      .catch((e) => !stale && setError(e instanceof Error ? e.message : "加载失败"));
    return () => {
      stale = true;
    };
  }, [token]);

  const toggle = (idx: number) => {
    const next = { ...done, [idx]: !done[idx] };
    setDone(next);
    try {
      localStorage.setItem(progressKey(token), JSON.stringify(next));
    } catch {
      /* ignore */
    }
  };

  const progress = useMemo(() => {
    if (!data) return { doneCount: 0, total: 0, pct: 0 };
    const total = data.items.length;
    const doneCount = data.items.filter((it, i) => done[i] ?? it.done).length;
    return { doneCount, total, pct: total ? Math.round((doneCount / total) * 100) : 0 };
  }, [data, done]);

  if (error) {
    return (
      <Shell>
        <div className="rounded-2xl border border-slate-200 bg-white p-10 text-center shadow-sm">
          <Share2 size={32} className="mx-auto text-slate-300" />
          <h1 className="mt-3 text-lg font-semibold text-slate-700">分享不可用</h1>
          <p className="mt-1 text-sm text-slate-500">
            {error.includes("过期") || error.includes("删除")
              ? "这个分享链接已过期或清单已被删除。"
              : "链接不存在或已被撤销。"}
          </p>
          <Link to="/" className="mt-4 inline-block text-sm font-medium text-brand-600 hover:underline">
            回到 StudyOS
          </Link>
        </div>
      </Shell>
    );
  }

  if (!data) {
    return (
      <Shell>
        <div className="flex justify-center py-16">
          <Spinner />
        </div>
      </Shell>
    );
  }

  return (
    <Shell>
      <div className="rounded-2xl border border-slate-200 bg-white shadow-sm">
        {/* 头部 */}
        <div className="border-b border-slate-100 px-6 py-5">
          <div className="flex items-center justify-between gap-2">
            <h1 className="min-w-0 truncate text-xl font-bold text-slate-800">{data.title}</h1>
            <span className="flex shrink-0 items-center gap-1 rounded-full bg-brand-50 px-2.5 py-1 text-[11px] font-medium text-brand-600">
              <Share2 size={12} /> 只读分享
            </span>
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-400">
            {data.course_name && (
              <span
                className="rounded-md px-2 py-0.5 text-[11px] font-medium text-white"
                style={{ backgroundColor: courseColor(data.course_name) }}
              >
                {data.course_name}
              </span>
            )}
            {data.tags.slice(0, 6).map((t) => (
              <span key={t} className="rounded bg-slate-100 px-1.5 py-0.5 text-slate-500">#{t}</span>
            ))}
            {data.due_at && (
              <span className={dueLabel(data.due_at).cls}>截止 {fmtDT(data.due_at)}（{dueLabel(data.due_at).text}）</span>
            )}
            <span>创建于 {fmtDT(data.created_at)}</span>
          </div>
          {data.description && <p className="mt-3 whitespace-pre-wrap text-sm leading-6 text-slate-600">{data.description}</p>}
        </div>

        {/* 进度 */}
        <div className="flex items-center gap-3 border-b border-slate-100 bg-slate-50/60 px-6 py-3">
          <div className="h-2 flex-1 overflow-hidden rounded-full bg-slate-200">
            <div
              className={`h-full rounded-full transition-all ${progress.pct === 100 ? "bg-emerald-500" : "bg-brand-500"}`}
              style={{ width: `${progress.pct}%` }}
            />
          </div>
          <span className="shrink-0 text-xs font-medium text-slate-500">
            {progress.doneCount}/{progress.total}（{progress.pct}%）
          </span>
        </div>

        {/* 清单项 */}
        <div className="space-y-1 px-6 py-4">
          {data.items.map((it, idx) => {
            const checked = done[idx] ?? it.done;
            return (
              <button
                key={idx}
                onClick={() => toggle(idx)}
                className="flex w-full items-start gap-3 rounded-xl px-2 py-2 text-left transition hover:bg-slate-50"
              >
                <span
                  className={`mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-md border-2 transition ${
                    checked ? "border-emerald-500 bg-emerald-500 text-white" : "border-slate-300"
                  }`}
                >
                  {checked && <Check size={12} />}
                </span>
                <span className="min-w-0 flex-1">
                  <span className={`text-sm ${checked ? "text-slate-400 line-through" : "text-slate-700"}`}>{it.text}</span>
                  {it.note && <span className="ml-2 rounded bg-amber-50 px-1.5 py-0.5 text-[11px] text-amber-600">{it.note}</span>}
                </span>
              </button>
            );
          })}
          {data.items.length === 0 && <p className="py-6 text-center text-sm text-slate-400">这是一张空清单</p>}
        </div>

        {/* 页脚 */}
        <div className="flex flex-wrap items-center gap-2 border-t border-slate-100 px-6 py-4">
          <p className="mr-auto flex items-center gap-1.5 text-[11px] text-slate-400">
            <Clock size={12} />
            {data.expires_at ? `有效期至 ${fmtDT(data.expires_at)}` : "永久有效"}
            {data.is_owner ? " · 这是你自己分享的清单" : " · 勾选进度只保存在你的浏览器"}
          </p>
          {!data.is_owner && (
            <>
              <button
                onClick={tryImport}
                className="flex items-center gap-1.5 rounded-xl bg-brand-600 px-3.5 py-2 text-xs font-medium text-white hover:bg-brand-700"
              >
                <HardDriveDownload size={14} /> 导入到我的 StudyOS
              </button>
              <button
                onClick={() => downloadJson(data, token)}
                className="flex items-center gap-1.5 rounded-xl border border-slate-200 bg-white px-3.5 py-2 text-xs font-medium text-slate-600 hover:bg-slate-50"
              >
                <Download size={14} /> 下载 JSON
              </button>
            </>
          )}
        </div>
        {importHint && <p className="border-t border-slate-100 px-6 py-3 text-xs text-amber-600">{importHint}</p>}
      </div>

      <p className="mt-4 flex items-center justify-center gap-1.5 text-xs text-slate-400">
        <GraduationCap size={14} />
        由 <Link to="/" className="font-medium text-brand-600 hover:underline">StudyOS</Link> 分享
        <span className="mx-1 text-slate-200">|</span>
        {data.view_count} 次查看
      </p>
    </Shell>
  );

  function tryImport() {
    const local = "http://127.0.0.1:8800";
    setImportHint("正在检测本机 StudyOS 服务…");
    fetch(`${local}/api/health`, { mode: "no-cors" })
      .then(() => {
        window.location.href = `${local}/#/checklists?import=${encodeURIComponent(
          `${window.location.origin}/api/share/${token}`,
        )}`;
      })
      .catch(() => {
        setImportHint("未检测到本机 StudyOS：请先在电脑上启动 StudyOS 再点导入，或「下载 JSON」后在「清单」页导入。");
      });
  }
}

function Shell({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-screen bg-slate-50">
      <div className="mx-auto max-w-2xl px-4 py-10">
        <div className="mb-5 flex items-center gap-2.5">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-brand-500 text-white">
            <GraduationCap size={20} />
          </div>
          <div>
            <div className="text-base font-bold tracking-tight text-slate-800">StudyOS</div>
            <div className="text-[10px] text-slate-400">个人学习管理智能体</div>
          </div>
        </div>
        {children}
      </div>
    </div>
  );
}

function downloadJson(data: SharedChecklist, token: string) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `studyos-share-${token.slice(0, 8)}.json`;
  a.click();
  URL.revokeObjectURL(a.href);
}
