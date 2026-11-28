import { ThumbsDown, ThumbsUp } from "lucide-react";
import { useState } from "react";
import { useAuth } from "./AuthProvider";
import { useLanguage } from "./LanguageSelector";
import { ApiError } from "../lib/api";
import { hub } from "../lib/hub";
import type { QueryResponse } from "../lib/types";
import { primaryButton, secondaryButton } from "../lib/ui";

// Thumbs up or down on an answer (team mode). A thumbs down asks what was wrong and queues it for the tech lead.
export default function AnswerFeedback({ projectId, question, result }: { projectId: string; question: string; result: QueryResponse }) {
  const { t } = useLanguage();
  const { mode } = useAuth();
  const [state, setState] = useState<"idle" | "commenting" | "sending" | "done" | "error">("idle");
  const [vote, setVote] = useState<"up" | "down" | null>(null);
  const [comment, setComment] = useState("");
  const [error, setError] = useState("");
  if (mode !== "team") return null;

  const send = async (v: "up" | "down", text = "") => {
    setVote(v);
    setState("sending");
    try {
      await hub.sendFeedback(projectId, {
        vote: v,
        question,
        answer: result.answer,
        sources: result.sources.map(({ code: _c, ...s }) => s),
        comment: text,
      });
      setState("done");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
      setState("error");
    }
  };

  if (state === "done") return <span className="text-xs text-muted">{t(vote === "down" ? "feedback_thanks_down" : "feedback_thanks_up")}</span>;
  const iconButton = (active: boolean) =>
    `flex h-[30px] w-[30px] items-center justify-center rounded-lg border ${active ? "border-accent text-accent" : "border-line-strong text-muted hover:text-ink"}`;
  return (
    <div className="flex w-full flex-col gap-2">
      <div className="flex items-center gap-1.5">
        <span className="text-xs text-muted">{t("feedback_ask")}</span>
        <button type="button" className={iconButton(vote === "up")} aria-label={t("feedback_up")} title={t("feedback_up")}
          disabled={state === "sending"} onClick={() => send("up")}>
          <ThumbsUp size={14} />
        </button>
        <button type="button" className={iconButton(vote === "down")} aria-label={t("feedback_down")} title={t("feedback_down")}
          disabled={state === "sending"} onClick={() => { setVote("down"); setState("commenting"); }}>
          <ThumbsDown size={14} />
        </button>
      </div>
      {state === "commenting" && (
        <div className="flex flex-col gap-2">
          <textarea
            aria-label={t("feedback_comment")}
            placeholder={t("feedback_comment")}
            maxLength={500}
            className="min-h-[70px] w-full rounded-lg border border-line-strong bg-surface p-2.5 text-sm outline-none placeholder:text-muted focus:border-accent"
            value={comment}
            onChange={(e) => setComment(e.target.value)}
          />
          <div className="flex gap-2">
            <button type="button" className={primaryButton} onClick={() => send("down", comment)}>
              {t("feedback_send")}
            </button>
            <button type="button" className={secondaryButton} onClick={() => { setVote(null); setState("idle"); }}>
              {t("review_cancel")}
            </button>
          </div>
        </div>
      )}
      {state === "error" && <p className="text-xs text-danger-ink">{error}</p>}
    </div>
  );
}
