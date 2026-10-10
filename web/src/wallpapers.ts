/**
 * 背景壁纸注册表 + 持久化
 *
 * 壁纸全部由 CSS 动画实现（纯本地渲染，不依赖任何外部图片/网络），
 * 符合本项目「本地优先 / 离线可用」的原则。选择结果保存在 localStorage
 * （属于浏览器展示偏好，随设备而非随数据同步）。
 *
 * 额外支持「自定义图片」壁纸：本地选图 → canvas 压缩 → base64 存入
 * localStorage，并支持开关动态（Ken Burns 缓慢缩放平移）效果。
 *
 * 通过 getWallpaper() 读取当前壁纸；setWallpaper(id) 切换并广播；
 * onWallpaperChange(cb) 订阅变更（返回取消订阅函数）。
 */

export interface Wallpaper {
  /** 唯一 id（同时是 localStorage 中的存储值） */
  id: string;
  name: string;
  desc: string;
  /** 设置页选择卡缩略图用的静态渐变（成本低、不跑重动画） */
  preview: string;
}

export const WALLPAPERS: Wallpaper[] = [
  {
    id: "default",
    name: "素净",
    desc: "默认浅色背景，干净专注",
    preview: "linear-gradient(135deg,#f4f5f9 0%,#e9ecf5 100%)",
  },
  {
    id: "aurora",
    name: "极光",
    desc: "柔和极光光带缓缓流动",
    preview: "linear-gradient(135deg,#eef2ff 0%,#fdf2f8 55%,#ecfeff 100%)",
  },
  {
    id: "gradient",
    name: "流光",
    desc: "色彩缓慢流动的渐变背景",
    preview: "linear-gradient(120deg,#e0e7ff 0%,#fbcfe8 50%,#cffafe 100%)",
  },
  {
    id: "nebula",
    name: "星云",
    desc: "柔和星云光晕缓慢旋转",
    preview: "linear-gradient(150deg,#f5f3ff 0%,#f0f9ff 100%)",
  },
  {
    id: "mist",
    name: "晨雾",
    desc: "轻柔晨雾缓缓飘动",
    preview: "linear-gradient(180deg,#eef6fb 0%,#f8fafc 100%)",
  },
  {
    id: "particles",
    name: "粒子",
    desc: "细微粒子静静悬浮",
    preview: "linear-gradient(180deg,#f8fafc 0%,#eef2ff 100%)",
  },
];

/** 自定义图片壁纸的特殊 id（不在预设数组中，单独处理） */
export const CUSTOM_WALLPAPER_ID = "custom";

export const WALLPAPER_STORAGE_KEY = "studyos.wallpaper";

/** 自定义图片壁纸的合成 Wallpaper 对象（不入 WALLPAPERS 数组） */
function customWallpaper(): Wallpaper {
  return { id: CUSTOM_WALLPAPER_ID, name: "自定义图片", desc: "", preview: "" };
}

/* ---------- 当前壁纸（预设） ---------- */

/** 从 localStorage 读取当前壁纸（读取失败 / 值非法时回退到默认） */
function load(): Wallpaper {
  try {
    const id = localStorage.getItem(WALLPAPER_STORAGE_KEY);
    if (id === CUSTOM_WALLPAPER_ID) return customWallpaper();
    return WALLPAPERS.find((w) => w.id === id) ?? WALLPAPERS[0];
  } catch {
    return WALLPAPERS[0];
  }
}

let current: Wallpaper = load();
const wallpaperListeners = new Set<(w: Wallpaper) => void>();

export function getWallpaper(): Wallpaper {
  return current;
}

/** 订阅壁纸变更（如设置页与壁纸层保持同步），返回取消订阅函数 */
export function onWallpaperChange(cb: (w: Wallpaper) => void): () => void {
  wallpaperListeners.add(cb);
  return () => {
    wallpaperListeners.delete(cb);
  };
}

/** 切换壁纸：写入 localStorage 并向所有订阅者广播 */
export function setWallpaper(id: string): void {
  if (id === CUSTOM_WALLPAPER_ID) {
    if (current.id === CUSTOM_WALLPAPER_ID) return;
    current = customWallpaper();
    try {
      localStorage.setItem(WALLPAPER_STORAGE_KEY, id);
    } catch {
      /* localStorage 不可用时仅本次会话生效 */
    }
    wallpaperListeners.forEach((cb) => cb(current));
    return;
  }
  const next = WALLPAPERS.find((w) => w.id === id);
  if (!next || next.id === current.id) return;
  current = next;
  try {
    localStorage.setItem(WALLPAPER_STORAGE_KEY, id);
  } catch {
    /* localStorage 不可用时仅本次会话生效 */
  }
  wallpaperListeners.forEach((cb) => cb(current));
}

/* ---------- 自定义图片壁纸 ---------- */

const CUSTOM_IMG_KEY = "studyos.wallpaper.custom";
const CUSTOM_DYNAMIC_KEY = "studyos.wallpaper.custom.dynamic";

const customImageListeners = new Set<(img: string | null) => void>();
const customDynamicListeners = new Set<(dyn: boolean) => void>();

/** 读取自定义图片（base64 data URL；未设置返回 null） */
export function getCustomImage(): string | null {
  try {
    return localStorage.getItem(CUSTOM_IMG_KEY);
  } catch {
    return null;
  }
}

/** 设置/清除自定义图片（传 null 清除），并向订阅者广播 */
export function setCustomImage(dataUrl: string | null): void {
  try {
    if (dataUrl == null) localStorage.removeItem(CUSTOM_IMG_KEY);
    else localStorage.setItem(CUSTOM_IMG_KEY, dataUrl);
  } catch {
    /* localStorage 不可用 / 超配额时忽略（调用方另行提示） */
  }
  customImageListeners.forEach((cb) => cb(getCustomImage()));
}

export function onCustomImageChange(cb: (img: string | null) => void): () => void {
  customImageListeners.add(cb);
  return () => {
    customImageListeners.delete(cb);
  };
}

/** 自定义图片是否启用动态（Ken Burns）效果（默认开启） */
export function getCustomDynamic(): boolean {
  try {
    return localStorage.getItem(CUSTOM_DYNAMIC_KEY) !== "0";
  } catch {
    return true;
  }
}

export function setCustomDynamic(dyn: boolean): void {
  try {
    localStorage.setItem(CUSTOM_DYNAMIC_KEY, dyn ? "1" : "0");
  } catch {
    /* ignore */
  }
  customDynamicListeners.forEach((cb) => cb(dyn));
}

export function onCustomDynamicChange(cb: (dyn: boolean) => void): () => void {
  customDynamicListeners.add(cb);
  return () => {
    customDynamicListeners.delete(cb);
  };
}

/* ---------- 图片压缩 ---------- */

/**
 * 把用户选择的图片压缩成适合作为背景的 base64 data URL。
 * - 等比缩放到最长边不超过 maxDim（默认 1440）
 * - JPEG quality 压缩（透明背景统一转白底，壁纸场景无碍）
 * - 限制体积，避免撑爆 localStorage 配额
 */
export async function compressImageFile(
  file: File,
  maxDim = 1440,
  quality = 0.82,
): Promise<string> {
  const url = URL.createObjectURL(file);
  try {
    const img = await new Promise<HTMLImageElement>((resolve, reject) => {
      const i = new Image();
      i.onload = () => resolve(i);
      i.onerror = () => reject(new Error("图片无法解析，请换一张试试"));
      i.src = url;
    });
    const scale = Math.min(1, maxDim / Math.max(img.naturalWidth, img.naturalHeight));
    const w = Math.max(1, Math.round(img.naturalWidth * scale));
    const h = Math.max(1, Math.round(img.naturalHeight * scale));
    const canvas = document.createElement("canvas");
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("无法创建画布");
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, w, h);
    ctx.drawImage(img, 0, 0, w, h);
    return canvas.toDataURL("image/jpeg", quality);
  } finally {
    URL.revokeObjectURL(url);
  }
}
