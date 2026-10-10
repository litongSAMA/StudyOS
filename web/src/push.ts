/**
 * Web Push 客户端：浏览器订阅（Service Worker push 订阅）→ 上报后端。
 * 与旧的页面内 `new Notification()` 不同——订阅后推送由后端经浏览器推送服务
 * 直达 Service Worker 的 push 事件，**页面关闭也能收到**。
 */
import { apiPush } from "./api";

/** base64url（无填充）→ Uint8Array（applicationServerKey 用） */
export function urlBase64ToUint8Array(s: string): Uint8Array {
  const padding = "=".repeat((4 - (s.length % 4)) % 4);
  const base64 = (s + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(base64);
  return Uint8Array.from([...raw].map((c) => c.charCodeAt(0)));
}

export interface PushSupport {
  supported: boolean;
  permission: NotificationPermission | "unsupported";
  reason?: string;
}

export function pushSupport(): PushSupport {
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
    return {
      supported: false,
      permission: "unsupported",
      reason: "当前浏览器不支持 Service Worker Push（需现代浏览器）",
    };
  }
  if (!window.isSecureContext) {
    return {
      supported: false,
      permission: "unsupported",
      reason: "非安全上下文：Web Push 需要 HTTPS 或 localhost 访问",
    };
  }
  if (!("Notification" in window)) {
    return {
      supported: false,
      permission: "unsupported",
      reason: "当前浏览器不支持系统通知",
    };
  }
  return {
    supported: true,
    permission: Notification.permission,
  };
}

async function getRegistration(): Promise<ServiceWorkerRegistration | null> {
  if (!("serviceWorker" in navigator)) return null;
  try {
    return await navigator.serviceWorker.ready;
  } catch {
    return navigator.serviceWorker
      .getRegistration()
      .then((r) => r ?? null)
      .catch(() => null);
  }
}

export async function getLocalSubscription(): Promise<PushSubscription | null> {
  const reg = await getRegistration();
  if (!reg) return null;
  try {
    return await reg.pushManager.getSubscription();
  } catch {
    return null;
  }
}

export interface PushStatus {
  support: PushSupport;
  permission: string;
  /** 本浏览器是否已有有效订阅 */
  localSubscribed: boolean;
  /** 后端推送库是否可用（pywebpush） */
  serverSupported: boolean;
  /** 服务端记录的订阅数 */
  serverSubscriptions: number;
  endpointMask: string;
  /** 推送消息 TTL（秒，>0 才能被 WNS 接受） */
  ttlSeconds: number;
  /** 调度/送达健康状态（后端内存态；degraded 时前端启用页面内兜底） */
  health: {
    last_tick_at: string | null;
    last_tick: { sent: number; removed: number; skipped: number; lib_available: boolean } | null;
    consecutive_failures: number;
    last_error: string | null;
    degraded: boolean;
  } | null;
}

export async function getPushStatus(): Promise<PushStatus> {
  const support = pushSupport();
  const [info, sub] = await Promise.all([
    apiPush.info().catch(() => null),
    getLocalSubscription(),
  ]);
  const endpoint = sub?.endpoint ?? "";
  return {
    support,
    permission: support.permission,
    localSubscribed: !!sub,
    serverSupported: info?.supported ?? false,
    serverSubscriptions: info?.subscriptions ?? 0,
    endpointMask: endpoint
      ? `${endpoint.slice(0, 30)}…${endpoint.slice(-6)}`
      : "",
    ttlSeconds: info?.ttl_seconds ?? 0,
    health: info?.health ?? null,
  };
}

/** 开启推送：申请通知权限 → pushManager.subscribe（VAPID 公钥）→ 上报后端 */
export async function enablePush(): Promise<PushSubscription> {
  const support = pushSupport();
  if (!support.supported) {
    throw new Error(support.reason ?? "当前环境不支持 Web Push");
  }
  const perm = await Notification.requestPermission();
  if (perm !== "granted") {
    throw new Error("未授予通知权限（可在浏览器站点设置里重新开启）");
  }
  const reg = await getRegistration();
  if (!reg) throw new Error("Service Worker 尚未就绪，请刷新页面后重试");
  const info = await apiPush.info();
  if (!info.vapid_public_key) throw new Error("后端未提供 VAPID 公钥");
  const sub = await reg.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: urlBase64ToUint8Array(info.vapid_public_key),
  });
  await apiPush.subscribe(sub.toJSON());
  return sub;
}

/** 关闭推送：本地取消订阅 + 服务端删除该订阅 */
export async function disablePush(): Promise<void> {
  const sub = await getLocalSubscription();
  if (sub) {
    await sub.unsubscribe();
    await apiPush.unsubscribe(sub.endpoint).catch(() => {
      /* 服务端可能已无此订阅，忽略 */
    });
  }
}
