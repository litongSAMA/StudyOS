import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { VitePWA } from "vite-plugin-pwa";

export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    VitePWA({
      // 自建 Service Worker（src/sw.ts）：预缓存之外还处理 push 事件——
      // 真正的 Web Push（页面关闭也能收提醒），generateSW 无法注入自定义 push 监听。
      strategies: "injectManifest",
      srcDir: "src",
      filename: "sw.ts",
      registerType: "autoUpdate",
      // 不注入默认的最简 registerSW.js，改由 main.tsx 手动注册并监听更新自动刷新，
      // 避免新版本部署后浏览器长期运行旧 bundle（用户看到"改动不生效"）。
      injectRegister: false,
      manifest: {
        name: "StudyOS Mobile · 移动学习管理",
        short_name: "StudyOS",
        description: "随时录入资料、查看 DDL、开始复习",
        lang: "zh-CN",
        theme_color: "#6366f1",
        background_color: "#f7f8fc",
        display: "standalone",
        start_url: "/",
        icons: [
          { src: "icon-192.png", sizes: "192x192", type: "image/png" },
          { src: "icon-512.png", sizes: "512x512", type: "image/png" },
        ],
      },
      includeAssets: ["icon.svg"],
      // 开发模式也注册 SW（localhost 是安全上下文，可直接测 push）
      devOptions: { enabled: true, type: "module" },
    }),
  ],
  server: {
    port: 5174,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8800",
        changeOrigin: true,
      },
    },
  },
});
