import { useState } from "react";
import { Check, LoaderCircle, Send } from "lucide-react";
import { apiAuth, type AuthVerificationChallenge, type ContactKind, ApiHttpError } from "../api";

const LABELS: Record<ContactKind, string> = {
  phone: "手机号",
  wechat: "微信号",
  qq: "QQ号",
};

interface Props {
  kind: ContactKind;
  value: string;
  code: string;
  challenge: AuthVerificationChallenge | null;
  purpose: "register" | "bind";
  disabled?: boolean;
  onValueChange: (value: string) => void;
  onCodeChange: (value: string) => void;
  onChallenge: (challenge: AuthVerificationChallenge | null) => void;
}

/** 联系方式 + 本机验证码输入。验证码发送器替换后，dev_code 展示会自然消失。 */
export default function ContactCodeField({
  kind,
  value,
  code,
  challenge,
  purpose,
  disabled = false,
  onValueChange,
  onCodeChange,
  onChallenge,
}: Props) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const label = LABELS[kind];

  const request = async () => {
    if (!value.trim()) return;
    setBusy(true);
    setError("");
    try {
      const next = await apiAuth.requestVerification(kind, value.trim(), purpose);
      onChallenge(next);
      onCodeChange("");
    } catch (err) {
      setError(err instanceof ApiHttpError ? err.message : "验证码发送失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-1.5 rounded-xl border border-slate-200 bg-white/70 p-2.5">
      <div className="flex items-center justify-between gap-2">
        <label className="text-[11px] font-medium text-slate-500">{label}（可选）</label>
        {challenge && <span className="flex items-center gap-1 text-[10px] text-emerald-600"><Check size={11} />验证码已发送</span>}
      </div>
      <div className="flex gap-1.5">
        <input
          value={value}
          onChange={(e) => {
            onValueChange(e.target.value);
            onChallenge(null);
            onCodeChange("");
          }}
          placeholder={kind === "phone" ? "+8613812345678" : kind === "wechat" ? "微信号" : "QQ号"}
          disabled={disabled}
          className="min-w-0 flex-1 rounded-lg border border-slate-200 px-2 py-1.5 text-xs outline-none focus:border-brand-400 focus:ring-2 focus:ring-brand-100 disabled:opacity-60"
        />
        <button
          type="button"
          onClick={request}
          disabled={disabled || busy || !value.trim()}
          className="flex shrink-0 items-center gap-1 rounded-lg border border-brand-200 bg-brand-50 px-2 py-1.5 text-[10px] font-medium text-brand-700 hover:border-brand-400 disabled:opacity-50"
        >
          {busy ? <LoaderCircle size={11} className="animate-spin" /> : <Send size={11} />}
          验证
        </button>
      </div>
      {challenge && (
        <div className="flex items-center gap-1.5">
          <input
            value={code}
            onChange={(e) => onCodeChange(e.target.value.replace(/\D/g, "").slice(0, 6))}
            placeholder="6 位验证码"
            inputMode="numeric"
            autoComplete="one-time-code"
            disabled={disabled}
            className="w-32 rounded-lg border border-emerald-200 bg-emerald-50/50 px-2 py-1.5 text-xs tracking-[0.2em] outline-none focus:border-emerald-400 disabled:opacity-60"
          />
          <span className="text-[10px] text-slate-400">有效期 {Math.ceil(challenge.expires_in / 60)} 分钟</span>
        </div>
      )}
      {challenge?.dev_code && (
        <div className="rounded-lg bg-amber-50 px-2 py-1 text-[10px] text-amber-700">
          本机验证码：<code className="font-mono font-bold tracking-[0.2em]">{challenge.dev_code}</code>
          <span className="ml-1 text-amber-600/70">（未接入外部发送服务）</span>
        </div>
      )}
      {error && <div className="text-[10px] text-red-500">{error}</div>}
    </div>
  );
}
