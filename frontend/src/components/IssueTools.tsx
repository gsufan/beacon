import { GitCommit, Link2, Radar, Users } from "lucide-react";
import { useEffect, useState } from "react";
import { Avatar } from "./Colorful";
import { useLanguage } from "./LanguageSelector";
import { ApiError } from "../lib/api";
import { hub, type Impact, type Issue, type IssueCode, type RelatedIssue } from "../lib/hub";
import type { SourceItem } from "../lib/types";
import { secondaryButton } from "../lib/ui";

export function functionTargets(sources: SourceItem[], max = 4): string[] {
  return [...new Set(sources.filter((s) => (s.chunk_type === "function" || s.chunk_type === "method") && s.name).map((s) => s.name))].slice(0, max);
}

const box = "flex flex-col gap-2 rounded-xl border border-line bg-surface px-4 py-3 text-sm";
const title = "flex items-center gap-1.5 text-[13px] font-semibold text-muted";

export function ImpactView({ impact }: { impact: Impact }) {
  const { t } = useLanguage();
  return (
    <div className="flex flex-col gap-2">
      {impact.targets.map((tg) => (
        <div key={tg.target} className="rounded-lg bg-surface-2 px-3 py-2">
          <div className="flex flex-wrap items-center gap-2">
            <code className="font-mono text-[13px] font-semibold">{tg.target}</code>
            {tg.untested && <span className="rounded-full bg-danger-bg px-2 py-px text-[11px] font-semibold text-danger-ink">{t("impact_untested")}</span>}
          </div>
          <p className="text-xs text-ink-2">
            {t("impact_counts", { direct: tg.callers.length, indirect: tg.indirect.length, tests: tg.tests.length })}
          </p>
          {tg.callers.length > 0 && (
            <p className="mt-1 text-xs">
              <span className="text-muted">{t("impact_called_by")} </span>
              {tg.callers.slice(0, 8).map((c) => `${c.name} (${c.file_path})`).join(", ")}
            </p>
          )}
          {tg.indirect.length > 0 && (
            <p className="text-xs">
              <span className="text-muted">{t("impact_indirect")} </span>
              {tg.indirect.slice(0, 6).map((c) => `${c.name} ← ${c.via}`).join(", ")}
            </p>
          )}
          {tg.tests.length > 0 && (
            <p className="text-xs">
              <span className="text-muted">{t("impact_tests")} </span>
              {tg.tests.slice(0, 6).map((c) => c.name).join(", ")}
            </p>
          )}
        </div>
      ))}
      {impact.docs && impact.docs.some((d) => d.status === "verified") && (
        <p className="text-xs text-ink-2">
          {t("impact_verified_docs", { files: impact.docs.filter((d) => d.status === "verified").map((d) => d.file_path).join(", ") })}
        </p>
      )}
      {impact.experts && impact.experts.length > 0 && (
        <p className="flex flex-wrap items-center gap-1.5 text-xs text-ink-2">
          <Users size={12} /> {t("impact_ask")}
          {impact.experts.map((e) => (
            <span key={e.name} className="inline-flex items-center gap-1">
              <Avatar name={e.name} size={16} /> {e.name}
            </span>
          ))}
        </p>
      )}
    </div>
  );
}

// Below an answer about an issue: its impact, the code already linked to it and similar issues (only a suggestion).
export default function IssueTools({ projectId, issue, sources }: { projectId: string; issue: Issue; sources: SourceItem[] }) {
  const { t } = useLanguage();
  const [impact, setImpact] = useState<Impact | null>(null);
  const [impactError, setImpactError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [code, setCode] = useState<IssueCode | null>(null);
  const [related, setRelated] = useState<RelatedIssue[] | null>(null);
  const targets = functionTargets(sources);

  useEffect(() => {
    setImpact(null);
    setCode(null);
    setRelated(null);
    hub.issueCode(projectId, issue.id).then(setCode, () => setCode(null));
    Promise.all([hub.issues(projectId, "all").catch(() => null), hub.closedIssues(projectId).catch(() => null)]).then(([open, closed]) => {
      const candidates = [...(open?.issues ?? []), ...(closed?.issues ?? [])];
      if (candidates.length === 0) return;
      hub.related(projectId, issue, candidates).then((r) => setRelated(r.related), () => setRelated(null));
    });
  }, [projectId, issue.id]);

  const runImpact = async () => {
    setBusy(true);
    setImpactError(null);
    try {
      setImpact(await hub.impact(projectId, targets));
    } catch (e) {
      setImpactError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      {targets.length > 0 && (
        <div className={box}>
          <span className={title}>
            <Radar size={14} /> {t("impact_title")}
          </span>
          {!impact && (
            <button type="button" className={`${secondaryButton} w-fit`} disabled={busy} onClick={runImpact}>
              {busy ? t("work_loading") : t("impact_run", { names: targets.join(", ") })}
            </button>
          )}
          {impactError && <p className="text-xs text-danger-ink">{impactError}</p>}
          {impact && <ImpactView impact={impact} />}
        </div>
      )}
      {code && code.commits.length > 0 && (
        <div className={box}>
          <span className={title}>
            <GitCommit size={14} /> {t("issue_code_title", { ref: issue.ref })}
          </span>
          <ul className="flex flex-col gap-1 text-xs">
            {code.commits.slice(0, 5).map((c) => (
              <li key={c.sha}>
                <span className="font-mono text-muted">{c.sha}</span> {c.message} · {c.author}
              </li>
            ))}
          </ul>
          <p className="text-xs text-ink-2">{t("issue_code_files", { files: code.files.slice(0, 6).map((f) => f.path).join(", ") })}</p>
        </div>
      )}
      {related && related.length > 0 && (
        <div className={box}>
          <span className={title}>
            <Link2 size={14} /> {t("related_title")}
          </span>
          <ul className="flex flex-col gap-1">
            {related.map((r) => (
              <li key={r.id} className="text-xs">
                <a href={r.url} target="_blank" rel="noreferrer" className="hover:underline">
                  <span className="font-mono text-muted">{r.ref}</span> {r.title}
                </a>{" "}
                <span className="text-muted">({r.state})</span>
              </li>
            ))}
          </ul>
          <p className="text-xs text-muted">{t("related_hint")}</p>
        </div>
      )}
    </>
  );
}
