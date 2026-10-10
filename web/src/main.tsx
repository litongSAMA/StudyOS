import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import "./index.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>
);

// PWA Service Worker 注册 + 自动更新
// sw.ts（self.skipWaiting + clientsClaim）在检测到新版本后会立即激活；
// 这里监听「新 SW 已激活」，自动刷新页面加载新构建产物——
// 避免新版本部署后，浏览器仍运行着旧的 bundle（表现为"改动不生效"）。
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker
      .register("/sw.js", { scope: "/" })
      .then((reg) => {
        // 首次访问（无旧 SW 控制）不触发刷新，避免加载即刷新
        if (!navigator.serviceWorker.controller) return;
        reg.addEventListener("updatefound", () => {
          const installing = reg.installing;
          if (!installing) return;
          installing.addEventListener("statechange", () => {
            if (
              installing.state === "activated" &&
              navigator.serviceWorker.controller
            ) {
              window.location.reload();
            }
          });
        });
      })
      .catch(() => {
        /* SW 不可用（非安全上下文等）时忽略，不影响页面功能 */
      });
  });
}
