import { useEffect, useRef, useState } from "react";
import { Check, ImagePlus, Loader2, Paintbrush, RefreshCw, Trash2 } from "lucide-react";
import {
  CUSTOM_WALLPAPER_ID,
  WALLPAPERS,
  compressImageFile,
  getCustomDynamic,
  getCustomImage,
  getWallpaper,
  onCustomDynamicChange,
  onCustomImageChange,
  onWallpaperChange,
  setCustomDynamic,
  setCustomImage,
  setWallpaper,
} from "../wallpapers";

/** 压缩后的图片体积上限（base64 字符数），超出则降低质量/尺寸重试 */
const MAX_DATA_URL_LEN = 4_500_000;

/**
 * 设置页「背景壁纸」选择卡片。
 * 内置 6 款动态壁纸 + 自定义图片（本地压缩存储，可开关 Ken Burns 动效）。
 * 点击即切换（实时预览整个应用背景），选择保存在 localStorage 并跨组件广播。
 */
export default function WallpaperPicker() {
  const [active, setActive] = useState(getWallpaper().id);
  const [customImg, setCustomImgState] = useState<string | null>(getCustomImage());
  const [customDynamic, setCustomDynamicState] = useState(getCustomDynamic());
  const [busy, setBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const offs = [
      onWallpaperChange((w) => setActive(w.id)),
      onCustomImageChange(setCustomImgState),
      onCustomDynamicChange(setCustomDynamicState),
    ];
    return () => offs.forEach((off) => off());
  }, []);

  /** 选择并压缩图片 → 保存为自定义壁纸并立即启用 */
  const pickImage = async (file: File | undefined) => {
    if (!file) return;
    if (!file.type.startsWith("image/")) {
      alert("请选择图片文件（JPG / PNG / WebP 等）。");
      return;
    }
    setBusy(true);
    try {
      // 先按较高规格压缩；体积超限则逐级降质量/尺寸重试
      let dataUrl = await compressImageFile(file, 1440, 0.82);
      if (dataUrl.length > MAX_DATA_URL_LEN) dataUrl = await compressImageFile(file, 1080, 0.72);
      if (dataUrl.length > MAX_DATA_URL_LEN) dataUrl = await compressImageFile(file, 800, 0.6);
      if (dataUrl.length > MAX_DATA_URL_LEN) {
        alert("图片太大，请换一张尺寸小一些的图片。");
        return;
      }
      setCustomImage(dataUrl);
      setWallpaper(CUSTOM_WALLPAPER_ID);
    } catch (e) {
      alert(`图片处理失败：${(e as Error).message}`);
    } finally {
      setBusy(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const removeCustom = () => {
    setCustomImage(null);
    // 若当前正使用自定义壁纸，移除后回退默认
    if (getWallpaper().id === CUSTOM_WALLPAPER_ID) setWallpaper("default");
  };

  const customActive = active === CUSTOM_WALLPAPER_ID;

  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-700">
        <Paintbrush size={16} className="text-brand-500" />
        背景壁纸
        <span className="text-[11px] font-normal text-slate-400">
          内置动态壁纸 + 自定义图片，选择即生效并自动保存在本机
        </span>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        {WALLPAPERS.map((w) => {
          const isActive = w.id === active;
          return (
            <button
              key={w.id}
              onClick={() => setWallpaper(w.id)}
              aria-pressed={isActive}
              title={`切换到「${w.name}」壁纸`}
              className={`group overflow-hidden rounded-xl border text-left transition ${
                isActive
                  ? "border-brand-500 ring-2 ring-brand-500/30"
                  : "border-slate-200 hover:border-brand-300 hover:shadow-sm"
              }`}
            >
              <div
                className="h-16 w-full"
                style={{ background: w.preview }}
                aria-hidden="true"
              />
              <div className="px-3 py-2">
                <div className="flex items-center gap-1.5 text-sm font-medium text-slate-700">
                  <span>{w.name}</span>
                  {isActive && (
                    <Check size={14} className="text-brand-600" strokeWidth={3} />
                  )}
                </div>
                <div className="mt-0.5 text-[11px] leading-4 text-slate-400">{w.desc}</div>
              </div>
            </button>
          );
        })}

        {/* 自定义图片：作为独立一个选项 */}
        <button
          onClick={() => {
            if (customImg) {
              setWallpaper(CUSTOM_WALLPAPER_ID);
            } else {
              fileRef.current?.click();
            }
          }}
          aria-pressed={customActive}
          disabled={busy}
          title={customImg ? "使用自定义图片壁纸" : "从本地选择一张图片作为背景"}
          className={`group relative overflow-hidden rounded-xl border text-left transition ${
            customActive
              ? "border-brand-500 ring-2 ring-brand-500/30"
              : "border-slate-200 hover:border-brand-300 hover:shadow-sm"
          } disabled:opacity-60`}
        >
          <div
            className="h-16 w-full"
            style={{
              background: customImg
                ? `url(${customImg}) center / cover`
                : "linear-gradient(135deg,#f1f5f9 0%,#e2e8f0 100%)",
            }}
            aria-hidden="true"
          />
          <div className="px-3 py-2">
            <div className="flex items-center gap-1.5 text-sm font-medium text-slate-700">
              <ImagePlus size={14} className="text-brand-500" />
              <span>自定义图片</span>
              {customActive && <Check size={14} className="text-brand-600" strokeWidth={3} />}
            </div>
            <div className="mt-0.5 text-[11px] leading-4 text-slate-400">
              {customImg ? "已选择图片（支持动态效果）" : "从本地选择一张图片"}
            </div>
          </div>
        </button>
      </div>

      {/* 自定义图片设置：已选图时展开 */}
      {customImg && (
        <div className="mt-3 rounded-xl border border-slate-200 bg-slate-50 p-3">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
            <span className="text-xs font-medium text-slate-600">动态效果</span>
            <button
              onClick={() => setCustomDynamic(!customDynamic)}
              aria-pressed={customDynamic}
              role="switch"
              aria-label="自定义图片动态效果开关"
              title={customDynamic ? "开启：缓慢缩放平移动效" : "关闭：静态背景"}
              className={`relative h-6 w-11 rounded-full transition ${
                customDynamic ? "bg-brand-600" : "bg-slate-300"
              }`}
            >
              <span
                className={`absolute top-0.5 h-5 w-5 rounded-full bg-white shadow transition-all ${
                  customDynamic ? "left-[22px]" : "left-0.5"
                }`}
              />
            </button>
            <span className="text-[11px] text-slate-400">
              {customDynamic ? "已开启（缓慢缩放平移）" : "已关闭（静态背景）"}
            </span>
            <div className="ml-auto flex items-center gap-2">
              <button
                onClick={() => fileRef.current?.click()}
                disabled={busy}
                className="flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-600 transition hover:border-brand-300 hover:text-brand-600 disabled:opacity-60"
              >
                {busy ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />}
                更换图片
              </button>
              <button
                onClick={removeCustom}
                className="flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-500 transition hover:border-red-200 hover:text-red-500"
              >
                <Trash2 size={13} />
                移除
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 隐藏的文件选择器 */}
      <input
        ref={fileRef}
        type="file"
        accept="image/*"
        className="hidden"
        onChange={(e) => pickImage(e.target.files?.[0])}
      />
    </div>
  );
}
