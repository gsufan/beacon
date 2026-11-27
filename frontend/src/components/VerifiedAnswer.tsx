import { BadgeCheck, TriangleAlert } from "lucide-react";
import { useState } from "react";
import { useAuth } from "./AuthProvider";
import { useLanguage } from "./LanguageSelector";
import MarkdownViewer from "./MarkdownViewer";
import { ApiError, request } from "../lib/api";
import type { QueryResponse, VerifiedMatch } from "../lib/types";
import { primaryButton, secondaryButton } from "../lib/ui";

// An answer a tech lead already verified for a similar question, shown before the model's answer.
export function VerifiedCard({ match }: { match: VerifiedMatch }) {
  const { t, language } = useLanguage();
  return (
    <article className="flex flex-col gap-2 rounded-[14px] border border-[var(--c4)] bg-[var(--c4-tint)] px-5 py-4">
      <div className="flex flex-wrap items-center gap-2 text-[13px] font-semibold text-[var(--c4)]">
        <BadgeCheck size={16} />
        {t(match.corrected ? "verified_card_corrected" : "verified_card", {
          name: match.reviewer,
          date: new Date(match.ts * 1000).toLocaleDateString(language),
        })}
      </div>
      <p className="text-xs text-ink-2">{t("verified_card_question", { q: match.question })}</p>
      <div className="rounded-lg bg-surface px-4 py-3">
        <MarkdownViewer content={match.answer} />
      </div>
      {match.stale_files.length > 0 && (
        <p className="flex items-center gap-1.5 text-xs text-danger-ink">
          <TriangleAlert size={13} /> {t("verified_card_stale", { files: match.stale_files.join(", ") })}
        </p>
      )}
    </article>
  );
}

// For a tech lead: verify an answer as it is, or correct it first. It is then offered for similar questions.
export function VerifyAnswerButton({ projectId, question, result }: { projectId: string; question: string; result: QueryResponse }) {
  const { t } = useLanguage();
  const { mode, roleIn } = useAuth();
  const role = roleIn(projectId);
  const [editing, setEditing] = useState<string | null>(null);
  const [state, setState] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  if (mode !== "team" || (role !== "admin" && role !== "tech_lead")) return null;
  const send = async (answer: string) => {
    setBusy(true);
    try {
      await request(`/projects/${projectId}/answers/verify`, {
        method: "POST",
        body: JSON.stringify({ question, answer, sources: result.sources.map(({ code: _c, ...s }) => s), corrected: answer !== result.answer }),
      });
      setState({ ok: true, text: t("verify_answer_done") });
      setEditing(null);
    } catch (e) {
      setState({ ok: false, text: e instanceof ApiError ? e.message : String(e) });
    } finally {
      setBusy(false);
    }
  };
  if (state?.ok) return <span className="text-xs font-semibold text-ok">{state.text}</span>;
  return (
    <div className="flex w-full flex-col gap-2">
      {editing === null ? (
        <div className="flex flex-wrap gap-2">
          <button type="button" className={secondaryButton} disabled={busy} onClick={() => send(result.answer)}>
            {t("verify_answer")}
          </button>
          <button type="button" className={secondaryButton} disabled={busy} onClick={() => setEditing(result.answer)}>
            {t("verify_answer_edit")}
          </button>
        </div>
      ) : (
        <>
          <textarea
            aria-label={t("verify_answer_edit")}
            className="min-h-[180px] w-full rounded-lg border border-line-strong bg-surface p-3 text-sm outline-none focus:border-accent"
            value={editing}
            onChange={(e) => setEditing(e.target.value)}
          />
          <div className="flex gap-2">
            <button type="button" className={primaryButton} disabled={busy || !editing.trim()} onClick={() => send(editing)}>
              {t("review_save_verify")}
            </button>
            <button type="button" className={secondaryButton} onClick={() => setEditing(null)}>
              {t("review_cancel")}
            </button>
          </div>
        </>
      )}
      {state && !state.ok && <p className="text-xs text-danger-ink">{state.text}</p>}
    </div>
  );
}
