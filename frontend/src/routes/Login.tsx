import { useState } from "react";
import { useAuth } from "../components/AuthProvider";
import { LanguageSelector, useLanguage } from "../components/LanguageSelector";
import { ApiError } from "../lib/api";
import { hub } from "../lib/hub";
import { inputClass, primaryButton } from "../lib/ui";

type Tab = "login" | "signup" | "invite";

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1 text-sm">
      <span className="text-xs font-semibold text-muted">{label}</span>
      {children}
    </label>
  );
}

export default function Login() {
  const { t } = useLanguage();
  const { signIn } = useAuth();
  const [tab, setTab] = useState<Tab>("login");
  const [form, setForm] = useState({ email: "", password: "", name: "", org_name: "", code: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, [k]: e.target.value });

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (tab === "login") signIn(await hub.login(form.email, form.password));
      else if (tab === "signup")
        signIn(await hub.signup({ org_name: form.org_name, name: form.name, email: form.email, password: form.password }));
      else signIn(await hub.acceptInvitation({ code: form.code, name: form.name, password: form.password }));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("settings_error_generic"));
    } finally {
      setBusy(false);
    }
  };

  const tabs: [Tab, string][] = [
    ["login", t("login_tab_login")],
    ["signup", t("login_tab_signup")],
    ["invite", t("login_tab_invite")],
  ];

  return (
    <div className="flex min-h-screen items-center justify-center bg-gradient-to-br from-[var(--side-bg)] via-[var(--side-bg-2)] to-[var(--hero-to)] px-4 text-ink">
      <div className="flex w-full max-w-[420px] flex-col gap-5">
        <div className="flex items-center justify-between">
          <div className="flex flex-col gap-0.5">
            <span className="font-display text-3xl font-bold tracking-wide text-white">Beacon</span>
            <span className="text-[13px] text-white/80">{t("login_tagline")}</span>
          </div>
          <LanguageSelector id="login-language" />
        </div>
        <div className="rounded-2xl border border-line bg-surface p-6 shadow-xl">
          <div role="tablist" className="mb-4 flex gap-1 rounded-lg bg-ground p-1">
            {tabs.map(([id, label]) => (
              <button
                key={id}
                role="tab"
                type="button"
                aria-selected={tab === id}
                onClick={() => {
                  setTab(id);
                  setError(null);
                }}
                className={`h-8 flex-1 rounded-md text-[13px] ${
                  tab === id ? "bg-surface font-semibold text-accent-ink shadow-sm" : "text-ink-2 hover:text-ink"
                }`}
              >
                {label}
              </button>
            ))}
          </div>
          <form onSubmit={submit} className="flex flex-col gap-3">
            {tab === "signup" && (
              <>
                <p className="text-[13px] text-ink-2">{t("login_signup_hint")}</p>
                <Field label={t("login_org_name")}>
                  <input className={inputClass} value={form.org_name} onChange={set("org_name")} required minLength={2} />
                </Field>
              </>
            )}
            {tab === "invite" && (
              <Field label={t("login_code")}>
                <input className={inputClass} value={form.code} onChange={set("code")} required />
              </Field>
            )}
            {tab !== "login" && (
              <Field label={t("login_name")}>
                <input className={inputClass} value={form.name} onChange={set("name")} required minLength={2} />
              </Field>
            )}
            {tab !== "invite" && (
              <Field label={t("login_email")}>
                <input className={inputClass} type="email" value={form.email} onChange={set("email")} required autoComplete="email" />
              </Field>
            )}
            <Field label={t("login_password")}>
              <input
                className={inputClass}
                type="password"
                value={form.password}
                onChange={set("password")}
                required
                minLength={tab === "login" ? 1 : 8}
                autoComplete={tab === "login" ? "current-password" : "new-password"}
              />
            </Field>
            {error && (
              <p role="alert" className="text-sm text-danger-ink">
                {error}
              </p>
            )}
            <button type="submit" disabled={busy} className={`${primaryButton} w-full`}>
              {busy ? t("login_working") : tab === "login" ? t("login_submit") : tab === "signup" ? t("login_create") : t("login_join")}
            </button>
          </form>
        </div>
        <p className="text-center text-xs text-white/75">{t("login_privacy")}</p>
      </div>
    </div>
  );
}
