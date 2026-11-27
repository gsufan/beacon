import { ShieldAlert } from "lucide-react";
import { useState } from "react";
import { useAuth } from "./AuthProvider";
import { useLanguage } from "./LanguageSelector";
import { ApiError } from "../lib/api";
import { hub } from "../lib/hub";
import { inputClass, primaryButton } from "../lib/ui";

// Full screen when this installation was revoked: its data was already wiped and it no longer serves.
export function InstanceRevoked() {
  const { t } = useLanguage();
  return (
    <div className="flex min-h-screen items-center justify-center bg-ground px-4 text-ink">
      <div className="flex max-w-md flex-col items-center gap-3 rounded-2xl border border-danger-line bg-surface p-8 text-center shadow-xl">
        <ShieldAlert size={36} className="text-danger-ink" />
        <h1 className="font-display text-xl font-semibold">{t("instance_revoked_title")}</h1>
        <p className="text-sm text-ink-2">{t("instance_revoked_body")}</p>
      </div>
    </div>
  );
}

// Banner while this Beacon Local is not registered with the Hub: an administrator registers a shared server; anyone
// can register their own machine as a personal installation (it is wiped if they leave the organization).
export function InstanceRegisterBanner() {
  const { t } = useLanguage();
  const { instance, isAdmin, reloadStatus } = useAuth();
  const [name, setName] = useState("");
  const [personal, setPersonal] = useState(!isAdmin);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  if (!instance || instance.registered) return null;
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await hub.registerInstance(name, personal);
      await reloadStatus();
      window.location.reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <form onSubmit={submit} className="flex flex-col gap-2 border-b border-line bg-accent-tint px-6 py-3 text-sm">
      <p className="font-semibold text-accent-ink">{t("instance_register_title")}</p>
      <p className="text-ink-2">{instance.blocked ?? t("instance_register_body")}</p>
      <div className="flex flex-wrap items-center gap-2">
        <input className={`${inputClass} max-w-xs`} required placeholder={t("instance_name")} value={name} onChange={(e) => setName(e.target.value)} />
        {isAdmin && (
          <label className="flex items-center gap-1.5 text-[13px]">
            <input type="checkbox" checked={personal} onChange={(e) => setPersonal(e.target.checked)} /> {t("instance_personal")}
          </label>
        )}
        <button type="submit" className={primaryButton} disabled={busy}>
          {t("instance_register")}
        </button>
      </div>
      {error && <p className="text-danger-ink">{error}</p>}
    </form>
  );
}
