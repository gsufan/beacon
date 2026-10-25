import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../lib/api";
import type { StorageProject } from "../lib/types";
import { useLanguage } from "./LanguageSelector";

type Kind = "clone" | "index" | "docs";

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v.toFixed(1)} ${units[i]}`;
}

const button = "rounded-lg border border-line px-2.5 py-1 text-xs text-ink-2 hover:bg-surface-2 disabled:opacity-50";

// Disk usage per project with selective deletion: the project stays registered and what was deleted is recreated on sync.
export default function StorageSection() {
  const { t } = useLanguage();
  const [projects, setProjects] = useState<StorageProject[] | null>(null);
  const [total, setTotal] = useState(0);
  const [pending, setPending] = useState<{ id: string; kind: Kind } | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const data = await api.storage();
      setProjects(data.projects);
      setTotal(data.total_bytes);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const confirmClean = async () => {
    if (!pending) return;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const out = await api.cleanProject(pending.id, { [pending.kind]: true });
      setMessage(t("storage_freed", { size: formatBytes(out.freed_bytes) }));
      setPending(null);
      await load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const kinds: { kind: Kind; label: string; bytes: (p: StorageProject) => number; available: (p: StorageProject) => boolean }[] = [
    { kind: "clone", label: t("storage_clone"), bytes: (p) => p.clone_bytes, available: (p) => p.managed_clone },
    { kind: "index", label: t("storage_index"), bytes: (p) => p.index_bytes, available: () => true },
    { kind: "docs", label: t("storage_docs"), bytes: (p) => p.docs_bytes, available: () => true },
  ];

  return (
    <div className="flex flex-col gap-3 rounded-[14px] border border-line bg-surface p-4">
      {projects === null && !error && <p className="text-sm text-muted">...</p>}
      {error && <p role="alert" className="text-sm text-danger-ink">{error}</p>}
      {message && <p role="status" className="text-sm text-ok">{message}</p>}
      {projects && projects.length === 0 && <p className="text-sm text-muted">{t("settings_no_projects")}</p>}
      {projects && projects.length > 0 && (
        <>
          <p className="text-sm text-ink-2">{t("storage_total", { size: formatBytes(total) })}</p>
          <ul className="flex flex-col gap-3">
            {projects.map((p) => (
              <li key={p.id} className="flex flex-col gap-2 rounded-lg border border-line p-3">
                <p className="text-sm font-semibold">{p.name} <span className="font-mono text-xs font-normal text-muted">{p.id}</span> · {formatBytes(p.total_bytes)}</p>
                <div className="flex flex-wrap gap-x-6 gap-y-2">
                  {kinds.map(({ kind, label, bytes, available }) => (
                    <div key={kind} className="flex flex-col gap-1 text-xs">
                      <span className="text-muted">{label}: {available(p) ? formatBytes(bytes(p)) : t("storage_yours")}</span>
                      {available(p) && pending?.id === p.id && pending.kind === kind ? (
                        <span className="flex max-w-xs flex-col gap-1.5">
                          <span role="note" className="text-ink-2">{t(`storage_warn_${kind}` as "storage_warn_clone")}</span>
                          <span className="flex gap-1.5">
                            <button type="button" className={button} disabled={busy} onClick={confirmClean}>{t("storage_confirm")}</button>
                            <button type="button" className={button} disabled={busy} onClick={() => setPending(null)}>{t("settings_cancel")}</button>
                          </span>
                        </span>
                      ) : available(p) && (
                        <button type="button" className={button} disabled={busy || bytes(p) === 0} aria-label={`${t("storage_delete")} ${label} ${p.id}`}
                          onClick={() => { setMessage(""); setPending({ id: p.id, kind }); }}>{t("storage_delete")}</button>
                      )}
                    </div>
                  ))}
                </div>
              </li>
            ))}
          </ul>
        </>
      )}
      <p className="text-xs text-muted">{t("storage_hint")}</p>
    </div>
  );
}
