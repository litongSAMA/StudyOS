import { useEffect, useState } from "react";
import { Bot, Eye, EyeOff, KeyRound, PlugZap, Sparkles } from "lucide-react";
import { apiAI } from "../api";
import type { AIConfig, AITestResult } from "../api";
import { useApp } from "../App";
import { Spinner } from "./ui";

const MASK = "••••••••";

export default function AIConfigCard() {
  const { refreshHealth } = useApp();
  const [cfg, setCfg] = useState<AIConfig | null>(null);
  const [preset, setPreset] = useState("");
  const [form, setForm] = useState({
    llm_base_url: "",
    llm_api_key: "",
    llm_model: "",
    embed_base_url: "",
    embed_api_key: "",
    embed_model: "",
  });
  const [showKey, setShowKey] = useState({ llm: false, embed: false });
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<AITestResult | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    apiAI.config().then((c) => {
      setCfg(c);
      setForm({
        llm_base_url: c.config.llm_base_url.value,
        llm_api_key: "",
        llm_model: c.config.llm_model.value,
        embed_base_url: c.config.embed_base_url.value,
        embed_api_key: "",
        embed_model: c.config.embed_model.value,
      });
    }).catch((e) => setMsg({ ok: false, text: `读取配置失败：${e.message}` }));
  }, []);

  const applyPreset = (key: string) => {
    setPreset(key);
    const p = cfg?.presets[key];
    if (!p) return;
    setForm((f) => ({
      ...f,
      llm_base_url: p.llm_base_url,
      llm_model: p.llm_model,
      embed_base_url: p.embed_base_url,
      embed_model: p.embed_model,
    }));
  };

  const save = async (clearLlmKey = false, clearEmbedKey = false) => {
    setSaving(true);
    setMsg(null);
    try {
      const next = await apiAI.save({
        llm_base_url: form.llm_base_url,
        llm_api_key: form.llm_api_key,
        llm_model: form.llm_model,
        embed_base_url: form.embed_base_url,
        embed_api_key: form.embed_api_key,
        embed_model: form.embed_model,
        clear_llm_key: clearLlmKey,
        clear_embed_key: clearEmbedKey,
      });
      setCfg(next);
      setForm((f) => ({ ...f, llm_api_key: "", embed_api_key: "" }));
      setMsg({ ok: true, text: "已保存并立即生效（无需重启）" });
      refreshHealth();
    } catch (e) {
      setMsg({ ok: false, text: `保存失败：${(e as Error).message}` });
    } finally {
      setSaving(false);
    }
  };

  const test = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      setTestResult(await apiAI.test({ use_saved: true }));
    } catch (e) {
      setMsg({ ok: false, text: `测试失败：${(e as Error).message}` });
    } finally {
      setTesting(false);
    }
  };

  const probeBadge = (label: string, p: AITestResult["chat"] | undefined) => {
    if (!p) return null;
    return (
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span
          className={`rounded-full px-2 py-0.5 font-medium ${
            p.ok ? "bg-emerald-100 text-emerald-700" : "bg-red-100 text-red-700"
          }`}
        >
          {label}：{p.ok ? "连通" : "失败"}
        </span>
        {p.ok && (
          <span className="text-slate-500">
            {p.latency_ms}ms
            {p.dim ? ` · ${p.dim} 维` : p.reply ? ` · 回复「${p.reply}」` : ""}
          </span>
        )}
        {!p.ok && <span className="text-red-600">{p.error}</span>}
      </div>
    );
  };

  const keyInput = (
    which: "llm" | "embed",
    value: string,
    onChange: (v: string) => void,
    saved: boolean,
  ) => (
    <div className="relative flex-1">
      <input
        type={showKey[which] ? "text" : "password"}
        placeholder={saved ? `${MASK}（已配置，留空保持不变）` : "API Key（留空则使用规则引擎/离线 TF-IDF）"}
        className="w-full rounded-xl border border-slate-200 px-3 py-2 pr-9 text-sm"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
      <button
        type="button"
        className="absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600"
        onClick={() => setShowKey((s) => ({ ...s, [which]: !s[which] }))}
      >
        {showKey[which] ? <EyeOff size={15} /> : <Eye size={15} />}
      </button>
    </div>
  );

  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="mb-1 flex items-center justify-between">
        <div className="flex items-center gap-2 text-sm font-semibold text-slate-700">
          <Bot size={16} className="text-brand-500" />
          AI 模型接入
        </div>
        {cfg && (
          <div className="flex items-center gap-2 text-[11px]">
            <span
              className={`rounded-full px-2 py-0.5 ${
                cfg.status.llm === "configured"
                  ? "bg-emerald-100 text-emerald-700"
                  : "bg-slate-100 text-slate-500"
              }`}
            >
              LLM：{cfg.status.llm === "configured" ? "已连接" : "规则引擎（离线）"}
            </span>
            <span className="rounded-full bg-slate-100 px-2 py-0.5 text-slate-500">
              语义检索：{cfg.status.semantic === "tfidf" ? "内置 TF-IDF（离线）" : cfg.status.semantic}
            </span>
          </div>
        )}
      </div>
      <p className="mb-4 text-[11px] text-slate-400">
        支持 OpenAI 兼容协议：DeepSeek / OpenAI / 硅基流动 / Kimi / 智谱 / 本地 Ollama（免 Key）。
        配置保存在本机数据库，随「多端同步」自动带到其他设备；密钥只存不回显。
      </p>

      {/* 服务商预设 */}
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <span className="text-xs text-slate-500">服务商预设：</span>
        {cfg &&
          Object.entries(cfg.presets).map(([key, p]) => (
            <button
              key={key}
              onClick={() => applyPreset(key)}
              className={`rounded-full px-3 py-1 text-xs transition ${
                preset === key
                  ? "bg-brand-600 text-white"
                  : "bg-slate-100 text-slate-600 hover:bg-brand-100 hover:text-brand-700"
              }`}
            >
              {p.label}
            </button>
          ))}
      </div>

      <div className="grid gap-3">
        {/* LLM */}
        <div className="rounded-xl bg-slate-50 p-3">
          <div className="mb-2 flex items-center gap-1.5 text-xs font-semibold text-slate-600">
            <Sparkles size={13} className="text-brand-500" /> 对话模型（抽取/检索扩展用）
          </div>
          <div className="flex flex-wrap gap-2">
            <input
              placeholder="Base URL，如 https://api.deepseek.com/v1"
              className="w-64 rounded-xl border border-slate-200 px-3 py-2 text-sm"
              value={form.llm_base_url}
              onChange={(e) => setForm({ ...form, llm_base_url: e.target.value })}
            />
            {keyInput("llm", form.llm_api_key, (v) => setForm({ ...form, llm_api_key: v }), !!cfg?.config.llm_api_key.set)}
            <input
              placeholder="模型名，如 deepseek-chat"
              className="w-44 rounded-xl border border-slate-200 px-3 py-2 text-sm"
              value={form.llm_model}
              onChange={(e) => setForm({ ...form, llm_model: e.target.value })}
            />
          </div>
        </div>

        {/* Embedding */}
        <div className="rounded-xl bg-slate-50 p-3">
          <div className="mb-2 flex items-center gap-1.5 text-xs font-semibold text-slate-600">
            <KeyRound size={13} className="text-brand-500" /> 语义向量接口（留空则用内置离线 TF-IDF，零配置可用）
          </div>
          <div className="flex flex-wrap gap-2">
            <input
              placeholder="Embedding Base URL（可留空）"
              className="w-64 rounded-xl border border-slate-200 px-3 py-2 text-sm"
              value={form.embed_base_url}
              onChange={(e) => setForm({ ...form, embed_base_url: e.target.value })}
            />
            {keyInput("embed", form.embed_api_key, (v) => setForm({ ...form, embed_api_key: v }), !!cfg?.config.embed_api_key.set)}
            <input
              placeholder="Embedding 模型名"
              className="w-44 rounded-xl border border-slate-200 px-3 py-2 text-sm"
              value={form.embed_model}
              onChange={(e) => setForm({ ...form, embed_model: e.target.value })}
            />
          </div>
        </div>

        {/* 操作 */}
        <div className="flex flex-wrap items-center gap-2">
          <button
            onClick={() => save()}
            disabled={saving || testing}
            className="flex items-center gap-1.5 rounded-xl bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-50"
          >
            {saving ? <Spinner /> : null} 保存配置
          </button>
          <button
            onClick={test}
            disabled={saving || testing}
            className="flex items-center gap-1.5 rounded-xl bg-slate-800 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50"
          >
            {testing ? <Spinner /> : <PlugZap size={15} />} 测试连接
          </button>
          {(cfg?.config.llm_api_key.set || cfg?.config.embed_api_key.set) && (
            <button
              onClick={() => save(true, true)}
              disabled={saving || testing}
              className="rounded-xl px-3 py-2 text-xs text-red-500 hover:bg-red-50"
            >
              清除已保存的 Key
            </button>
          )}
          {cfg && (
            <span className="text-[11px] text-slate-400">
              来源 db=页面配置 · env=环境变量 · default=内置默认
            </span>
          )}
        </div>

        {msg && (
          <div className={`rounded-xl px-3 py-2 text-xs ${msg.ok ? "bg-emerald-50 text-emerald-700" : "bg-red-50 text-red-600"}`}>
            {msg.text}
          </div>
        )}

        {testResult && (
          <div className="space-y-1 rounded-xl border border-slate-100 bg-slate-50 px-3 py-2">
            {probeBadge("对话接口", testResult.chat)}
            {probeBadge("向量接口", testResult.embed)}
            {testResult.embed.ok === false && form.embed_base_url === "" && (
              <div className="text-[11px] text-slate-400">未配置向量接口时自动使用内置离线 TF-IDF，语义检索依然可用。</div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
