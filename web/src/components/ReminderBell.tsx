import { useEffect, useState } from "react";
import { Bell } from "lucide-react";
import { apiPush, apiTasks } from "../api";
import type { Task } from "../types";

/**
 * 站内提醒：每分钟轮询即将到期的任务。
 * - 侧边栏铃铛角标显示 24h 内到期数量
 * - 页面内系统通知作为**未开启 Web Push / 推送链路异常（degraded）时的兜底**
 *   （localStorage 去重）。Web Push 健康时由后端调度 + Service Worker push
 *   弹通知（关页也能收），这里不再弹，避免一次到期弹两条。
 */
export default function ReminderBell() {
  const [dueSoon, setDueSoon] = useState<Task[]>([]);

  useEffect(() => {
    let mounted = true;

    const pushActive = async (): Promise<boolean> => {
      try {
        if (!("serviceWorker" in navigator) || !("PushManager" in window)) return false;
        const reg = await navigator.serviceWorker.ready;
        return !!(await reg.pushManager.getSubscription());
      } catch {
        return false;
      }
    };

    const check = async () => {
      try {
        const tasks = await apiTasks.upcoming(24);
        if (!mounted) return;
        setDueSoon(tasks.filter((t) => !t.done));
        // 兜底判定：没有 Web Push 订阅，或订阅在但推送链路异常（后端连续发送失败，
        // 调度器/测试通知都送不到）→ 用页面内 Notification，避免提醒被静默吞掉
        const [active, info] = await Promise.all([
          pushActive(),
          apiPush.info().catch(() => null),
        ]);
        const pushHealthy = active && !(info?.health?.degraded);
        if (
          !pushHealthy &&
          "Notification" in window &&
          Notification.permission === "granted"
        ) {
          const fired = JSON.parse(localStorage.getItem("studyos_fired") ?? "{}");
          for (const t of tasks) {
            if (!t.done && !fired[t.id]) {
              new Notification("StudyOS · DDL 提醒", {
                body: `${t.title} 即将截止${t.course_name ? `（${t.course_name}）` : ""}`,
                tag: `task-${t.id}`,
              });
              fired[t.id] = 1;
              localStorage.setItem("studyos_fired", JSON.stringify(fired));
            }
          }
        }
      } catch {
        /* 后端未启动时静默 */
      }
    };
    check();
    const timer = setInterval(check, 60_000);
    return () => {
      mounted = false;
      clearInterval(timer);
    };
  }, []);

  return (
    <div className="relative inline-flex" title="24 小时内到期的任务">
      <Bell size={16} className={dueSoon.length ? "text-red-500" : "text-slate-400"} />
      {dueSoon.length > 0 && (
        <span className="absolute -right-2 -top-1.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-red-500 px-1 text-[9px] font-bold text-white">
          {dueSoon.length}
        </span>
      )}
    </div>
  );
}
