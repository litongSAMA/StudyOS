import { useEffect, useRef, useState } from "react";
import {
  Archive,
  Check,
  Copy,
  Download,
  Eye,
  EyeOff,
  Link2,
  RefreshCw,
  ShieldAlert,
  Smartphone,
  Trash2,
  Upload,
} from "lucide-react";
import { apiSync } from "../api";
import type { SyncResult, SyncStatus } from "../api";
import { useApp } from "../App";
import { Spinner } from "./ui";

const fmtCounts = (c: { created: number; updated: number; deleted: number; skipped: number; files_written: number; files_skipped: number }) =>
  `新增 ${c.created} · 更新 ${c.updated} · 删除 ${c.deleted} · 跳过 ${c.skipped}` +
  (c.files_written || c.files_skipped ? ` · 文件同步 ${c.files_written} / 跳过 ${c.files_skipped}` : "");

/** 明文令牌自动隐藏倒计时（秒）：避免停在屏幕上被旁人/录屏看到 */
const REVEAL_SECONDS = 60;

export default function SyncCard() {
  const { bump } = useApp();
  const [status, setStatus] = useState<SyncStatus | null>(null);
  const [remoteUrl, setRemoteUrl] = useState("");
  const [remoteToken, setRemoteToken] = useState("");
  const [includeFiles, setIncludeFiles] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [backingUp, setBackingUp] = useState(false);
  const [result, setResult] = useState<SyncResult | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [copied, setCopied] = useState(false);
  const [plainToken, setPlainToken] = useState("");
  const [countdown, setCountdown] = useState(0);
  const restoreRef = useRef<HTMLInputElement>(null);

  const load = () =>
    apiSync.status().then((s) => {
      setStatus(s);
      setRemoteUrl(s.remote_url);
    }).catch((e) => setMsg({ ok: false, text: `读取同步状态失败：${e.message}` }));

  useEffect(() => {
    load();
  }, []);

  // 明文令牌只在屏幕上停留 REVEAL_SECONDS 秒
  useEffect(() => {
    if (!plainToken) return;
    if (countdown <= 0) {
      setPlainToken("");
      return;
    }
    const t = setTimeout(() => setCountdown((n) => n - 1), 1000);
    return () => clearTimeout(t);
  }, [plainToken, countdown]);

  const reveal = async () => {
    if (plainToken) {           // 再点一次 = 立即隐藏
      setPlainToken("");
      setCountdown(0);
      return;
    }
    try {
      const r = await apiSync.revealToken();
      setPlainToken(r.token);
      setCountdown(REVEAL_SECONDS);
      setMsg({ ok: true, text: r.warning });
    } catch (e) {
      setMsg({ ok: false, text: (e as Error).message });
    }
  };

  const copyToken = async () => {
    try {
      // 复制也走显式披露接口：不显示在屏幕上，但同样是一次「知情」的取用
      const r = await apiSync.revealToken();
      await navigator.clipboard.writeText(r.token);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
      setMsg({ ok: true, text: `已复制到剪贴板（指纹 ${r.fingerprint}）。${r.warning}` });
    } catch (e) {
      setMsg({ ok: false, text: `复制失败：${(e as Error).message}` });
    }
  };

  const regenerate = async () => {
    if (!confirm("重新生成令牌后，其他设备上的旧令牌将失效，需要重新配对。继续？")) return;
    try {
      await apiSync.regenerateToken();
      setPlainToken("");
      setCountdown(0);
      await load();
      setMsg({ ok: true, text: "已重新生成令牌，请在其他设备重新填入新令牌" });
    } catch (e) {
      setMsg({ ok: false, text: `重新生成失败：${(e as Error).message}` });
    }
  };

  const syncNow = async () => {
    if (!remoteUrl.trim() || !remoteToken.trim()) {
      setMsg({ ok: false, text: "请填写远端设备地址与它的同步令牌（在另一台设备的「设置 → 多端同步」查看）" });
      return;
    }
    setSyncing(true);
    setMsg(null);
    setResult(null);
    try {
      const r = await apiSync.now(remoteUrl.trim(), remoteToken.trim(), includeFiles);
      setResult(r);
      setMsg({ ok: true, text: "同步完成，两台设备数据已合并" });
      await load();
      bump();
    } catch (e) {
      setMsg({ ok: false, text: `同步失败：${(e as Error).message}` });
    } finally {
      setSyncing(false);
    }
  };

  const clearRemote = async () => {
    await apiSync.clearRemote();
    await load();
    setMsg({ ok: true, text: "已清除远端设备配置" });
  };

  const downloadBackup = async () => {
    setMsg(null);
    setBackingUp(true);
    try {
      // 备份下载受 X-Sync-Token 保护：由 apiSync.backup 取令牌并带头发起
      await apiSync.backup();
    } catch (e) {
      setMsg({ ok: false, text: `备份下载失败：${(e as Error).message}` });
    } finally {
      setBackingUp(false);
    }
  };

  const restore = async (file: File) => {
    if (!confirm(`从备份恢复会覆盖当前全部数据（课程/资料/任务/复习计划），且不可撤销。确定恢复「${file.name}」？`)) {
      if (restoreRef.current) restoreRef.current.value = "";
      return;
    }
    setMsg(null);
    try {
      const r = await apiSync.restore(file);
      setMsg({ ok: true, text: `恢复完成：${fmtCounts(r.stats)}` });
      await load();
      bump();
    } catch (e) {
      setMsg({ ok: false, text: `恢复失败：${(e as Error).message}` });
    } finally {
      if (restoreRef.current) restoreRef.current.value = "";
    }
  };

  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="mb-1 flex items-center gap-2 text-sm font-semibold text-slate-700">
        <Smartphone size={16} className="text-brand-500" />
        多端同步（两台设备都运行 StudyOS 即可互传）
      </div>
      <p className="mb-4 text-[11px] text-slate-400">
        点对点直连、不经过任何云端：手机/平板/另一台电脑打开 StudyOS，在它上面查看令牌，
        填入下方即可一键同步。冲突按「最后修改时间」自动解决；数据含原文与附件文件。
      </p>

      {/* 本机令牌：默认掩码，明文需显式点击（后端只允许本机披露并留下记录） */}
      <div className="mb-4 rounded-xl bg-slate-50 p-3">
        <div className="mb-1.5 flex flex-wrap items-center gap-2 text-xs font-semibold text-slate-600">
          本机令牌（给其他设备用）
          <span className="rounded bg-white px-1.5 py-0.5 text-[10px] font-normal text-slate-400 ring-1 ring-slate-200">
            指纹 {status?.token_fingerprint ?? "…"}
          </span>
          {status?.token_source === "env" && (
            <span className="rounded bg-amber-50 px-1.5 py-0.5 text-[10px] font-normal text-amber-600">
              来自 SYNC_TOKEN 环境变量（重新生成无效）
            </span>
          )}
          {plainToken && (
            <span className="rounded bg-red-50 px-1.5 py-0.5 text-[10px] font-normal text-red-500">
              明文显示中 · {countdown}s 后自动隐藏
            </span>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <code
            className={`flex-1 min-w-52 truncate rounded-lg border px-3 py-2 text-xs ${
              plainToken
                ? "border-red-200 bg-red-50/60 text-slate-800"
                : "border-slate-200 bg-white text-slate-500"
            }`}
          >
            {status ? (plainToken || `${status.token_masked}（${status.token_length} 位，已隐藏）`) : "加载中…"}
          </code>
          <button
            onClick={reveal}
            className={iconBtn}
            title={plainToken ? "立即隐藏" : "显示明文令牌（仅配对时使用）"}
            disabled={!status?.token_reveal_allowed}
          >
            {plainToken ? <EyeOff size={15} /> : <Eye size={15} />}
          </button>
          <button onClick={copyToken} className={iconBtn} title="复制令牌到剪贴板" disabled={!status?.token_reveal_allowed}>
            {copied ? <Check size={15} className="text-emerald-600" /> : <Copy size={15} />}
          </button>
          <button onClick={regenerate} className="rounded-lg border border-slate-200 px-2.5 py-2 text-xs text-slate-500 hover:border-red-300 hover:text-red-500">
            重新生成
          </button>
        </div>
        <div className="mt-2 flex items-start gap-1.5 rounded-lg bg-amber-50/70 px-2.5 py-2 text-[11px] text-amber-700">
          <ShieldAlert size={13} className="mt-0.5 shrink-0" />
          <span>
            {status?.token_warning ??
              "令牌等同于本机数据的完整读写权限，只在配对时向自己的设备展示。"}
            {status && !status.token_reveal_allowed && (
              <>
                <br />
                当前页面不是从这台设备本机打开的（来源非 127.0.0.1），为防止令牌被局域网内其它设备抓取，
                明文查看已被禁用；请在运行 StudyOS 的设备上打开本页，或以 <code>ALLOW_REMOTE_ADMIN=1</code> 启动后端。
              </>
            )}
          </span>
        </div>
        <div className="mt-1.5 text-[11px] text-slate-400">设备 ID：{status?.device_id ?? "…"}</div>
      </div>

      {/* 远端设备 */}
      <div className="rounded-xl bg-slate-50 p-3">
        <div className="mb-1.5 flex items-center gap-1.5 text-xs font-semibold text-slate-600">
          <Link2 size={13} className="text-brand-500" /> 远端设备（另一台 StudyOS 的地址 + 它的令牌）
        </div>
        <div className="flex flex-wrap gap-2">
          <input
            placeholder="地址，如 http://192.168.1.10:8800"
            className="w-64 rounded-xl border border-slate-200 px-3 py-2 text-sm"
            value={remoteUrl}
            onChange={(e) => setRemoteUrl(e.target.value)}
          />
          <input
            placeholder="远端令牌"
            className="w-64 rounded-xl border border-slate-200 px-3 py-2 text-sm"
            value={remoteToken}
            onChange={(e) => setRemoteToken(e.target.value)}
          />
          <label className="flex cursor-pointer items-center gap-1.5 text-xs text-slate-600">
            <input
              type="checkbox"
              checked={includeFiles}
              onChange={(e) => setIncludeFiles(e.target.checked)}
            />
            同步附件文件（≤32MB/个）
          </label>
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <button
            onClick={syncNow}
            disabled={syncing}
            className="flex items-center gap-1.5 rounded-xl bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-50"
          >
            {syncing ? <Spinner /> : <RefreshCw size={15} />} 立即同步
          </button>
          {status?.remote_token_set && (
            <button onClick={clearRemote} className="flex items-center gap-1 rounded-xl px-3 py-2 text-xs text-slate-400 hover:text-red-500">
              <Trash2 size={13} /> 清除远端配置
            </button>
          )}
          <span className="text-[11px] text-slate-400">
            本端数据：{status ? `课程 ${status.counts.courses} · 资料 ${status.counts.materials} · 任务 ${status.counts.tasks} · 复习计划 ${status.counts.reviews}` : "…"}
          </span>
        </div>
      </div>

      {result && (
        <div className="mt-3 space-y-1 rounded-xl border border-slate-100 bg-slate-50 px-3 py-2 text-xs text-slate-600">
          <div>← 推送到远端：{fmtCounts(result.pushed)}</div>
          <div>→ 从远端拉取：{fmtCounts(result.pulled)}</div>
          <div className="text-[11px] text-slate-400">
            {result.incremental ? "增量同步" : "全量同步"}
            {typeof result.rounds === "number" ? ` · ${result.rounds} 轮往返` : ""}
            {" · 只传变更条目，附件按内容哈希去重"}
          </div>
        </div>
      )}
      {msg && (
        <div className={`mt-3 rounded-xl px-3 py-2 text-xs ${msg.ok ? "bg-emerald-50 text-emerald-700" : "bg-red-50 text-red-600"}`}>
          {msg.text}
        </div>
      )}

      {/* 备份 / 恢复 */}
      <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-slate-100 pt-4">
        <span className="flex items-center gap-1.5 text-xs font-semibold text-slate-600">
          <Archive size={14} className="text-brand-500" /> 完整备份 / 恢复（换设备、防丢失）
        </span>
        <button
          onClick={downloadBackup}
          disabled={backingUp}
          className="flex items-center gap-1 rounded-lg border border-slate-200 px-3 py-1.5 text-xs text-slate-600 transition hover:border-brand-400 hover:text-brand-600 disabled:opacity-50"
        >
          {backingUp ? <Spinner /> : <Download size={13} />} 下载备份 zip（数据 + 附件）
        </button>
        <button
          onClick={() => restoreRef.current?.click()}
          className="flex items-center gap-1 rounded-lg border border-slate-200 px-3 py-1.5 text-xs text-slate-600 transition hover:border-brand-400 hover:text-brand-600"
        >
          <Upload size={13} /> 从备份恢复
        </button>
        <input
          ref={restoreRef}
          type="file"
          accept=".zip"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) restore(f);
          }}
        />
      </div>
    </div>
  );
}

const iconBtn =
  "rounded-lg border border-slate-200 px-2.5 py-2 text-slate-400 transition hover:border-brand-400 hover:text-brand-600 disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:border-slate-200 disabled:hover:text-slate-400";
