import { useEffect, useState } from "react";
import { Bell, BellOff, Download, Send } from "lucide-react";
import { apiPush, exportUrl } from "../api";
import { disablePush, enablePush, getPushStatus } from "../push";
import type { PushSupport, PushStatus } from "../push";
import DownloadLink from "./DownloadLink";
import { Spinner } from "./ui";

/**
 * Web Push 设置卡片：真正的推送 = 浏览器 Service Worker push 订阅，
 * 后端到点经浏览器推送服务送达——**关掉页面也能收到**（不再是页面内 Notification）。
 * 手机端 / 不支持的环境用 iCal 导出兜底（导入系统日历，自带 VALARM 提前提醒）。
 */
export default function PushCard() {
  const [status, setStatus] = useState<PushStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const refresh = () => {
    getPushStatus().then(setStatus).catch(console.error);
  };

  useEffect(refresh, []);

  const onEnable = async () => {
    setBusy(true);
    setMsg(null);
    try {
      await enablePush();
      setMsg({
        ok: true,
        text: "已开启 Web Push：现在关掉本页面也能收到 DDL 提醒（由浏览器推送服务送达）。",
      });
      refresh();
    } catch (e) {
      setMsg({ ok: false, text: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(false);
    }
  };

  const onTest = async () => {
    setBusy(true);
    setMsg(null);
    try {
      const r = await apiPush.test();
      const failed = r.results.filter((x) => !x.ok);
      if (r.sent > 0 && failed.length === 0) {
        setMsg({ ok: true, text: `测试通知已发出（${r.sent} 个订阅），应已弹出系统通知。` });
      } else if (failed.length > 0) {
        // 逐个订阅回报失败原因，避免只显示第一条把问题藏起来
        const lines = failed.map(
          (f) => `· ${f.endpoint}：${f.error ?? "未知错误"}`,
        );
        setMsg({ ok: false, text: `发送失败（${failed.length}/${r.results.length} 个订阅）：\n${lines.join("\n")}` });
      } else if (!r.lib_available) {
        setMsg({ ok: false, text: "后端未安装推送库（pywebpush），请安装后重启。" });
      } else {
        setMsg({ ok: false, text: "还没有推送订阅，请先点击「开启推送」。" });
      }
      refresh();
    } catch (e) {
      setMsg({ ok: false, text: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(false);
    }
  };

  const onDisable = async () => {
    setBusy(true);
    setMsg(null);
    try {
      await disablePush();
      setMsg({ ok: true, text: "已关闭本浏览器的 Web Push（提醒仍保留站内角标，可用 iCal 兜底）。" });
      refresh();
    } catch (e) {
      setMsg({ ok: false, text: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(false);
    }
  };

  const unsupported = (status?.support as PushSupport | undefined)?.supported === false;

  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="mb-1 flex items-center gap-2 text-sm font-semibold text-slate-700">
        <Bell size={16} className="text-brand-500" />
        DDL 提醒 · Web Push
      </div>
      <p className="mb-3 text-[11px] text-slate-400">
        默认提前 3 天 / 1 天 / 2 小时；侧边栏铃铛显示 24 小时内到期任务。开启后由后端每分钟
        检查提醒点，经浏览器推送服务送到 Service Worker——页面关闭也能收到。
      </p>

      {unsupported ? (
        <div className="rounded-xl bg-amber-50 px-3 py-2.5 text-xs text-amber-700">
          {status?.support.reason ?? "当前环境不支持 Web Push（需 HTTPS 或 localhost + 现代浏览器）。"}
          <br />
          兜底方案：手机 / 不支持的环境建议使用 iCal 导出，导入系统日历即可收到带提前提醒（VALARM）的通知。
        </div>
      ) : (
        <>
          <ul className="mb-3 space-y-1 text-xs text-slate-500">
            <li>
              · 通知权限：{status?.permission === "granted" ? "已授权" : status?.permission === "denied" ? "已拒绝（需在浏览器站点设置里重新开启）" : "未申请"}
            </li>
            <li>
              · 本浏览器订阅：{status?.localSubscribed ? `已开启 ${status.endpointMask}` : "未开启"}
            </li>
            <li>· 服务端订阅数：{status?.serverSubscriptions ?? 0}</li>
            {status && !status.serverSupported && (
              <li className="text-amber-600">· 后端推送库（pywebpush）未安装，订阅虽可保存但无法送达</li>
            )}
            {status?.health && (
              <li className={status.health.degraded ? "text-amber-600" : ""}>
                · 调度状态：
                {status.health.degraded ? (
                  <>
                    最近推送失败（{status.health.last_error ?? "未知错误"}），已自动启用页面内兜底提醒
                  </>
                ) : (
                  <>
                    正常
                    {status.health.last_tick_at
                      ? `（最近检查 ${new Date(status.health.last_tick_at).toLocaleTimeString()}）`
                      : "（调度器待首轮检查）"}
                  </>
                )}
              </li>
            )}
          </ul>

          <div className="flex flex-wrap items-center gap-2">
            {!status?.localSubscribed ? (
              <button
                onClick={onEnable}
                disabled={busy}
                className="flex items-center gap-1.5 rounded-xl bg-slate-800 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50"
              >
                {busy ? <Spinner /> : <Bell size={15} />} 开启推送
              </button>
            ) : (
              <>
                <button
                  onClick={onTest}
                  disabled={busy}
                  className="flex items-center gap-1.5 rounded-xl bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-50"
                >
                  {busy ? <Spinner /> : <Send size={15} />} 发送测试通知
                </button>
                <button
                  onClick={onDisable}
                  disabled={busy}
                  className="flex items-center gap-1.5 rounded-xl border border-slate-200 px-4 py-2 text-sm font-medium text-slate-600 hover:bg-slate-50 disabled:opacity-50"
                >
                  <BellOff size={15} /> 关闭推送
                </button>
              </>
            )}
          </div>

          <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-slate-100 pt-3">
            <DownloadLink
              path={exportUrl("tasks", "ical")}
              fallbackName="studyos-tasks.ics"
              className="flex items-center gap-1.5 rounded-xl border border-slate-200 px-3 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-50"
            >
              <Download size={13} /> 下载 iCal（兜底）
            </DownloadLink>
            <span className="text-[11px] text-slate-400">
              把 DDL 导入手机 / 电脑系统日历（VALARM 提前提醒），与 Web Push 互为兜底
            </span>
          </div>
        </>
      )}

      {msg && (
        <div
          className={`mt-3 rounded-xl px-3 py-2 text-xs ${
            msg.ok ? "bg-emerald-50 text-emerald-700" : "bg-red-50 text-red-600"
          }`}
        >
          {msg.text}
        </div>
      )}
    </div>
  );
}
