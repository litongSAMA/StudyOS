import { useState, type MouseEvent, type ReactNode } from "react";
import { downloadFile, reportDownloadError } from "../api";

/**
 * 下载链接：外观与普通 `<a>` 完全一致，点击时改为 `fetch` + Blob 触发下载。
 *
 * 为什么不直接让 `<a href="/api/...">` 导航：
 * 1. Service Worker 的 SPA 回退会把 `/api/*` 的导航请求换成 index.html —— 点导出
 *    只会把页面换成一个空壳，文件永远下不来（本组件的诞生原因）；
 * 2. 登录保护开启时，导航请求不带 Authorization 头，只会拿到 401 JSON；
 * 3. 失败（后端没启动等）能给出可见提示，而不是静默无反应。
 *
 * 仍保留 href/download 属性：Service Worker 尚未更新、或 JS 未生效时，
 * 浏览器原生下载行为依旧可用（有 download 属性的链接不会被 SPA 回退吃掉）。
 */
export default function DownloadLink({
  path,
  fallbackName,
  className,
  title,
  children,
  onDone,
  onError,
  disabled = false,
}: {
  /** 后端下载地址（如 `/api/export/tasks/csv`） */
  path: string;
  /** 服务端未给 Content-Disposition 时的兜底文件名 */
  fallbackName: string;
  className?: string;
  title?: string;
  children: ReactNode;
  /** 下载成功（参数为实际落盘文件名） */
  onDone?: (filename: string) => void;
  /** 下载失败（后端未连接 / 401 / 422…）：传了就地展示，不传则走全局横幅提示 */
  onError?: (message: string) => void;
  disabled?: boolean;
}) {
  const [busy, setBusy] = useState(false);

  const onClick = async (e: MouseEvent<HTMLAnchorElement>) => {
    e.preventDefault();
    if (busy || disabled) return;
    setBusy(true);
    try {
      const name = await downloadFile(path, fallbackName);
      reportDownloadError(null);
      onDone?.(name);
    } catch (err) {
      const message = (err as Error)?.message || "下载失败，请稍后重试";
      if (onError) onError(message);
      else reportDownloadError(`${fallbackName} 下载失败：${message}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <a
      href={path}
      download={fallbackName}
      title={title}
      aria-busy={busy}
      aria-disabled={disabled || busy}
      onClick={onClick}
      className={`${className ?? ""} ${
        disabled || busy ? "pointer-events-none opacity-50" : ""
      }`}
    >
      {children}
    </a>
  );
}
