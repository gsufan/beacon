import { CheckCircle2, FilePen, Sparkles } from "lucide-react";
import { useState } from "react";
import { useAuth } from "./AuthProvider";
import { useLanguage } from "./LanguageSelector";
import { ApiError, request } from "../lib/api";
import type { DocReviewInfo } from "../lib/types";
import { primaryButton, secondaryButton } from "../lib/ui";

const STATUS_STYLE: Record<string, string> = {
  draft: "bg-[var(--c3-tint)] text-[var(--c3)]",
  verified: "bg-[var(--c4-tint)] text-[var(--c4)]",
  pending: "bg-[var(--c2-tint)] text-[var(--c2)]",
};

export function ReviewBadge({ status }: { status: string }) {
  const { t } = useLanguage();
  return (
    <span className={`inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-px text-[11px] font-semibold ${STATUS_STYLE[status] ?? ""}`}>
      {status === "verified" ? <CheckCircle2 size={11} /> : status === "draft" ? <Sparkles size={11} /> : <FilePen size={11} />}
      {t(`review_${status}` as "review_draft")}
    </span>
  );
}

// Header and body of a generated document: its review state and, for a tech lead, verify / correct / accept draft.
export default function DocReview({
  projectId,
  filePath,
  content,
  review,
  draft,
  onChanged,
  children,
}: {
  projectId: string;
  filePath: string;
  content: string;
  review: DocReviewInfo | null;
  draft: string | null;
  onChanged: () => void;
  children: React.ReactNode;
}) {
  const { t, language } = useLanguage();
  const { mode, roleIn } = useAuth();
  const role = roleIn(projectId);
  const canVerify = mode === "team" && (role === "admin" || role === "tech_lead");
  const [editing, setEditing] = useState<string | null>(null);
  const [showDraft, setShowDraft] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const q = `/projects/${projectId}/docs?file_path=${encodeURIComponent(filePath)}`;

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      setEditing(null);
      setShowDraft(false);
      onChanged();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  const save = (text: string) => run(() => request(q, { method: "PUT", body: JSON.stringify({ content: text }) }));
  const verify = () =>
    run(() => request(`/projects/${projectId}/docs/verify?file_path=${encodeURIComponent(filePath)}`, { method: "POST" }));

  const status = mode === "team" ? (review?.status ?? "draft") : null;
  const when = review?.ts ? new Date(review.ts * 1000).toLocaleDateString(language) : "";

  return (
    <>
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-6 py-3">
        <span className="break-all font-mono text-xs text-muted">{filePath}</span>
        <div className="flex flex-wrap items-center gap-2">
          {status && <ReviewBadge status={status} />}
          {canVerify && editing === null && (
            <>
              {status !== "verified" && !draft && (
                <button type="button" className={secondaryButton} disabled={busy} onClick={verify}>
                  {t("review_verify")}
                </button>
              )}
              <button type="button" className={secondaryButton} disabled={busy} onClick={() => setEditing(content)}>
                {t("review_edit")}
              </button>
            </>
          )}
        </div>
      </div>
      {status && review?.reviewer && (
        <p className="border-b border-line bg-surface-2 px-6 py-2 text-xs text-ink-2">
          {status === "verified"
            ? t(review.corrected ? "review_by_corrected" : "review_by", { name: review.reviewer, date: when })
            : t("review_pending_hint", { name: review.reviewer, date: when })}
        </p>
      )}
      {status === "draft" && <p className="border-b border-line bg-surface-2 px-6 py-2 text-xs text-ink-2">{t("review_draft_hint")}</p>}
      {draft && (
        <div className="flex flex-wrap items-center gap-2 border-b border-line bg-[var(--c2-tint)] px-6 py-2 text-xs">
          <span>{t("review_new_draft")}</span>
          <button type="button" className={secondaryButton} onClick={() => setShowDraft(!showDraft)}>
            {showDraft ? t("review_hide_draft") : t("review_show_draft")}
          </button>
          {canVerify && (
            <button type="button" className={secondaryButton} disabled={busy} onClick={() => setEditing(draft)}>
              {t("review_use_draft")}
            </button>
          )}
        </div>
      )}
      {error && <p className="px-6 pt-3 text-sm text-danger-ink">{error}</p>}
      <div className="px-6 py-5">
        {editing !== null ? (
          <div className="flex flex-col gap-2">
            <textarea
              aria-label={t("review_editor")}
              className="min-h-[420px] w-full rounded-lg border border-line-strong bg-surface p-3 font-mono text-[13px] outline-none focus:border-accent"
              value={editing}
              onChange={(e) => setEditing(e.target.value)}
            />
            <div className="flex gap-2">
              <button type="button" className={primaryButton} disabled={busy || !editing.trim()} onClick={() => save(editing)}>
                {t("review_save_verify")}
              </button>
              <button type="button" className={secondaryButton} onClick={() => setEditing(null)}>
                {t("review_cancel")}
              </button>
            </div>
          </div>
        ) : showDraft && draft ? (
          <DraftView text={draft} />
        ) : (
          children
        )}
      </div>
    </>
  );
}

function DraftView({ text }: { text: string }) {
  return <pre className="mt-2 whitespace-pre-wrap rounded-lg bg-surface-2 p-3 font-mono text-xs text-ink-2">{text}</pre>;
}
