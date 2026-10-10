import { useEffect, useState } from "react";
import { Check, Link2, LoaderCircle, Trash2 } from "lucide-react";
import {
  apiAuth,
  ApiHttpError,
  type AuthContact,
  type AuthVerificationChallenge,
  type ContactKind,
} from "../api";
import ContactCodeField from "./ContactCodeField";

const KINDS: ContactKind[] = ["phone", "wechat", "qq"];

/** 已登录账号的联系方式绑定/验证换绑面板。 */
export default function AccountContacts() {
  const [contacts, setContacts] = useState<AuthContact[]>([]);
  const [values, setValues] = useState<Record<ContactKind, string>>({ phone: "", wechat: "", qq: "" });
  const [codes, setCodes] = useState<Record<ContactKind, string>>({ phone: "", wechat: "", qq: "" });
  const [challenges, setChallenges] = useState<Record<ContactKind, AuthVerificationChallenge | null>>({ phone: null, wechat: null, qq: null });
  const [currentPassword, setCurrentPassword] = useState("");
  const [busy, setBusy] = useState<ContactKind | "load" | null>("load");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const load = async () => {
    try {
      const next = await apiAuth.contacts();
      setContacts(next);
      setValues((prev) => {
        const copy = { ...prev };
        next.forEach((item) => { copy[item.kind] = item.value ?? ""; });
        return copy;
      });
    } catch (err) {
      setError(err instanceof ApiHttpError ? err.message : "联系方式加载失败");
    } finally {
      setBusy(null);
    }
  };

  useEffect(() => { void load(); }, []);

  const save = async (kind: ContactKind) => {
    const challenge = challenges[kind];
    if (!values[kind].trim() || !challenge || codes[kind].length !== 6 || !currentPassword) return;
    setBusy(kind);
    setError("");
    setMessage("");
    try {
      const result = await apiAuth.bindContact(kind, values[kind].trim(), challenge.challenge_id, codes[kind], currentPassword);
      setContacts(result.contacts);
      setChallenges((prev) => ({ ...prev, [kind]: null }));
      setCodes((prev) => ({ ...prev, [kind]: "" }));
      setCurrentPassword("");
      setMessage(`${result.contacts.find((item) => item.kind === kind)?.label ?? "联系方式"}已验证并绑定`);
    } catch (err) {
      setError(err instanceof ApiHttpError ? err.message : "联系方式绑定失败");
    } finally {
      setBusy(null);
    }
  };

  const remove = async (kind: ContactKind) => {
    const item = contacts.find((contact) => contact.kind === kind);
    if (!item?.bound || !currentPassword) return;
    if (!confirm(`解绑${item.label}？解绑后仍可使用用户名和密码登录。`)) return;
    setBusy(kind);
    setError("");
    setMessage("");
    try {
      const result = await apiAuth.unbindContact(kind, currentPassword);
      setContacts(result.contacts);
      setValues((prev) => ({ ...prev, [kind]: "" }));
      setCurrentPassword("");
      setMessage(`${item.label}已解绑`);
    } catch (err) {
      setError(err instanceof ApiHttpError ? err.message : "联系方式解绑失败");
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="rounded-xl border border-slate-100 bg-slate-50/60 p-3">
      <div className="mb-2 flex items-center gap-1.5 text-xs font-semibold text-slate-600">
        <Link2 size={13} className="text-brand-500" /> 联系方式与验证换绑
      </div>
      <p className="mb-2 text-[10px] leading-relaxed text-slate-400">
        换绑需要当前密码和新联系方式验证码；验证码在本机模式下直接显示。
      </p>
      {(error || message) && (
        <div className={`mb-2 rounded-lg px-2.5 py-1.5 text-[11px] ${error ? "bg-red-50 text-red-600" : "bg-emerald-50 text-emerald-700"}`}>
          {error || message}
        </div>
      )}
      <div className="mb-2">
        <input
          type="password"
          value={currentPassword}
          onChange={(e) => setCurrentPassword(e.target.value)}
          placeholder="当前密码（绑定或解绑时必填）"
          autoComplete="current-password"
          className="w-full rounded-lg border border-slate-200 bg-white px-2.5 py-2 text-xs outline-none focus:border-brand-400 focus:ring-2 focus:ring-brand-100"
        />
      </div>
      {busy === "load" ? (
        <div className="flex items-center gap-1 text-[11px] text-slate-400"><LoaderCircle size={13} className="animate-spin" />加载中…</div>
      ) : (
        <div className="grid gap-2 md:grid-cols-3">
          {KINDS.map((kind) => {
            const item = contacts.find((contact) => contact.kind === kind);
            return (
              <div key={kind} className="space-y-1.5 rounded-lg border border-slate-200 bg-white p-2">
                {item?.bound && (
                  <div className="flex items-center justify-between text-[10px] text-emerald-600">
                    <span className="flex items-center gap-1"><Check size={11} />已验证：{item.masked}</span>
                    <button type="button" onClick={() => void remove(kind)} disabled={busy !== null || !currentPassword} title="解绑" className="text-slate-300 hover:text-red-500 disabled:opacity-40"><Trash2 size={12} /></button>
                  </div>
                )}
                <ContactCodeField
                  kind={kind}
                  value={values[kind]}
                  code={codes[kind]}
                  challenge={challenges[kind]}
                  purpose="bind"
                  disabled={busy !== null}
                  onValueChange={(value) => setValues((prev) => ({ ...prev, [kind]: value }))}
                  onCodeChange={(value) => setCodes((prev) => ({ ...prev, [kind]: value }))}
                  onChallenge={(challenge) => setChallenges((prev) => ({ ...prev, [kind]: challenge }))}
                />
                <button type="button" onClick={() => void save(kind)} disabled={busy !== null || !currentPassword || !challenges[kind] || codes[kind].length !== 6} className="flex w-full items-center justify-center gap-1 rounded-lg bg-brand-600 px-2 py-1.5 text-[10px] font-medium text-white hover:bg-brand-700 disabled:opacity-40">
                  {busy === kind ? <LoaderCircle size={11} className="animate-spin" /> : <Link2 size={11} />} 保存并验证换绑
                </button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
