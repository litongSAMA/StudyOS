import { useEffect, useState } from "react";
import {
  CUSTOM_WALLPAPER_ID,
  getWallpaper,
  getCustomImage,
  getCustomDynamic,
  onWallpaperChange,
  onCustomImageChange,
  onCustomDynamicChange,
  type Wallpaper,
} from "../wallpapers";

/**
 * 全屏动态壁纸层。
 * 挂在 Layout 根部（fixed、z-0、不拦截事件），主内容与侧边栏在其上方半透明展示。
 * 「素净」为默认浅色（复用 body 背景），不渲染多余层。
 * 「自定义图片」渲染用户选择的图片，可按开关启用 Ken Burns 缓慢缩放平移动效。
 */

/** 粒子配置：位置 / 尺寸 / 时长 / 延迟，固定值保证两次渲染一致 */
const PARTICLES: { left: string; size: number; dur: number; delay: number; color: string }[] = [
  { left: "8%", size: 5, dur: 18, delay: 0, color: "#a5b4fc" },
  { left: "16%", size: 3, dur: 24, delay: 4, color: "#c4b5fd" },
  { left: "27%", size: 6, dur: 20, delay: 8, color: "#67e8f9" },
  { left: "36%", size: 4, dur: 26, delay: 1, color: "#f9a8d4" },
  { left: "48%", size: 5, dur: 22, delay: 6, color: "#a5b4fc" },
  { left: "58%", size: 3, dur: 19, delay: 11, color: "#fbbf24" },
  { left: "68%", size: 6, dur: 25, delay: 3, color: "#c4b5fd" },
  { left: "77%", size: 4, dur: 21, delay: 9, color: "#67e8f9" },
  { left: "86%", size: 5, dur: 23, delay: 2, color: "#f9a8d4" },
  { left: "93%", size: 3, dur: 27, delay: 7, color: "#a5b4fc" },
];

/** 极光 / 星云 / 晨雾 使用的柔光斑块数量 */
const BLOB_COUNTS: Record<string, number> = {
  aurora: 4,
  nebula: 3,
  mist: 3,
};

function Aurora() {
  return (
    <>
      {Array.from({ length: BLOB_COUNTS.aurora }).map((_, i) => (
        <span key={i} className={`wpblob wp-aurora-blob wp-aurora-blob-${i}`} />
      ))}
    </>
  );
}

function Nebula() {
  return (
    <>
      {Array.from({ length: BLOB_COUNTS.nebula }).map((_, i) => (
        <span key={i} className={`wpblob wp-nebula-blob wp-nebula-blob-${i}`} />
      ))}
    </>
  );
}

function Mist() {
  return (
    <>
      {Array.from({ length: BLOB_COUNTS.mist }).map((_, i) => (
        <span key={i} className={`wpblob wp-mist-blob wp-mist-blob-${i}`} />
      ))}
    </>
  );
}

function Particles() {
  return (
    <>
      {PARTICLES.map((p, i) => (
        <span
          key={i}
          className="wp-particle"
          style={{
            left: p.left,
            width: p.size,
            height: p.size,
            background: p.color,
            animationDuration: `${p.dur}s`,
            animationDelay: `${p.delay}s`,
          }}
        />
      ))}
    </>
  );
}

function WallpaperVisual({ id }: { id: string }) {
  switch (id) {
    case "aurora":
      return <Aurora />;
    case "nebula":
      return <Nebula />;
    case "mist":
      return <Mist />;
    case "particles":
      return <Particles />;
    // gradient / default：纯 CSS 背景，无需子元素
    default:
      return null;
  }
}

export default function WallpaperLayer() {
  const [wp, setWp] = useState<Wallpaper>(getWallpaper());
  const [customImg, setCustomImg] = useState<string | null>(getCustomImage());
  const [customDynamic, setCustomDynamic] = useState(getCustomDynamic());

  useEffect(() => {
    const offs = [
      onWallpaperChange(setWp),
      onCustomImageChange(setCustomImg),
      onCustomDynamicChange(setCustomDynamic),
    ];
    return () => offs.forEach((off) => off());
  }, []);

  // 自定义图片：渲染所选图片（未选图时复用 body 默认背景）
  if (wp.id === CUSTOM_WALLPAPER_ID) {
    if (!customImg) return null;
    return (
      <div
        className="wallpaper-layer wp-custom"
        aria-hidden="true"
        data-wallpaper={CUSTOM_WALLPAPER_ID}
      >
        <img
          src={customImg}
          alt=""
          draggable={false}
          className={`wp-custom-img${customDynamic ? " wp-custom-img-animated" : ""}`}
        />
      </div>
    );
  }

  // 素净：默认浅色，复用 body 背景，不渲染多余层
  if (wp.id === "default") return null;

  // 预设动态壁纸
  return (
    <div
      className={`wallpaper-layer wp-${wp.id}`}
      aria-hidden="true"
      data-wallpaper={wp.id}
    >
      <WallpaperVisual id={wp.id} />
    </div>
  );
}
