import { Eye, GitCommit } from "lucide-react";
import { useEffect, useState } from "react";
import { useLanguage } from "./LanguageSelector";
import { forgetFollow } from "../lib/follows";
import { hub, type FollowStatus } from "../lib/hub";

// Followed code that changed since the person last looked; nothing is shown when nothing changed.
export default function FollowedPanel({ projectId }: { projectId: string }) {
  const { t } = useLanguage();
  const [status, setStatus] = useState<FollowStatus | null>(null);
  const reload = () => hub.follows(projectId).then(setStatus, () => setStatus(null));
  useEffect(() => {
    setStatus(null);
    void reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  if (!status || status.changed === 0) return null;
  const changed = status.files.filter((f) => f.changes.length > 0);
  const seen = (file?: string) => hub.followsSeen(projectId, file).then(reload, () => {});
  const unfollow = (file: string) =>
    hub.unfollow(projectId, file).then(() => {
      forgetFollow(file);
      return reload();
    }, () => {});

  return (
    <section aria-label={t("follow_title")} className="flex flex-col gap-2.5 rounded-xl border border-accent bg-accent-tint px-4 py-3 text-sm">
      <div className="flex items-center justify-between gap-2">
        <h2 className="flex items-center gap-1.5 text-[13px] font-semibold text-accent-ink">
          <Eye size={14} /> {t("follow_title_changed", { n: status.changed })}
        </h2>
        <button type="button" className="text-xs font-medium text-accent-ink hover:underline" onClick={() => seen()}>
          {t("follow_seen_all")}
        </button>
      </div>
      <ul className="flex flex-col gap-2.5">
        {changed.map((f) => (
          <li key={f.file_path} className="flex flex-col gap-1">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="break-all font-mono text-xs font-semibold text-ink">{f.file_path}</span>
              <span className="flex gap-3 text-xs">
                <button type="button" className="text-accent-ink hover:underline" onClick={() => seen(f.file_path)}>
                  {t("follow_seen")}
                </button>
                <button type="button" className="text-muted hover:text-ink" onClick={() => unfollow(f.file_path)}>
                  {t("follow_stop")}
                </button>
              </span>
            </div>
            <ul className="flex flex-col gap-0.5">
              {f.changes.map((c) => (
                <li key={c.sha} className="flex gap-1.5 text-xs text-ink-2">
                  <GitCommit size={12} className="mt-0.5 shrink-0 text-muted" />
                  <span className="min-w-0 break-words">
                    {c.subject} <span className="text-muted">· {c.author} · {c.date}</span>
                  </span>
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ul>
    </section>
  );
}
