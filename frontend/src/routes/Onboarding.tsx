import { BookOpen, ExternalLink, Footprints, ListChecks, Users } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router";
import { useAuth } from "../components/AuthProvider";
import { Avatar } from "../components/Colorful";
import { useLanguage } from "../components/LanguageSelector";
import MarkdownViewer from "../components/MarkdownViewer";
import { useProject } from "../components/ProjectSelector";
import { ApiError } from "../lib/api";
import { hub, type Issue, type OnboardingRoute } from "../lib/hub";
import { cardClass, primaryButton } from "../lib/ui";

type Run = { status: "idle" } | { status: "loading" } | { status: "ok"; data: OnboardingRoute } | { status: "error"; message: string };

const RADIUS_CLASS: Record<string, string> = {
  small: "bg-[var(--c4-tint)] text-[var(--c4)]",
  medium: "bg-surface-2 text-ink-2",
  large: "bg-danger-bg text-danger-ink",
  unknown: "bg-surface-2 text-muted",
};

// Route for someone new to the project: what to read first, the domain's vocabulary and good first issues.
export default function Onboarding() {
  const { t, language } = useLanguage();
  const { mode } = useAuth();
  const { projectId, projects } = useProject();
  const project = projects.find((p) => p.id === projectId);
  const [run, setRun] = useState<Run>({ status: "idle" });
  useEffect(() => setRun({ status: "idle" }), [projectId]);

  const build = async () => {
    if (!projectId) return;
    setRun({ status: "loading" });
    let issues: Issue[] = [];
    if (mode === "team") {
      try {
        issues = (await hub.issues(projectId, "all")).issues.slice(0, 12);
      } catch {
        issues = []; // no backlog linked: the route still has the reading order and the glossary
      }
    }
    try {
      setRun({ status: "ok", data: await hub.onboarding(projectId, { issues, language }) });
    } catch (e) {
      setRun({ status: "error", message: e instanceof ApiError ? e.message : String(e) });
    }
  };

  const r = run.status === "ok" ? run.data : null;
  return (
    <div className="flex flex-col gap-6 px-10 pb-10 pt-6">
      <div className="flex flex-col gap-1 rounded-2xl bg-gradient-to-r from-[var(--hero-from)] to-[var(--hero-to)] px-6 py-5 text-white shadow-sm">
        <h1 className="font-display text-[24px] font-semibold">{t("onb_title")}</h1>
        <p className="text-[13px] text-white/85">{project ? t("onb_subtitle", { project: project.name }) : t("no_projects")}</p>
      </div>
      {projectId && (
        <section className={`${cardClass} flex max-w-4xl flex-col gap-5`}>
          <div className="flex flex-wrap items-center gap-3">
            <button type="button" className={primaryButton} disabled={run.status === "loading"} onClick={build}>
              {run.status === "loading" ? t("work_loading") : r ? t("onb_rebuild") : t("onb_build")}
            </button>
            {run.status === "loading" && <span className="text-sm text-ink-2">{t("onb_loading")}</span>}
          </div>
          {run.status === "error" && (
            <p role="alert" className="rounded-xl border border-danger-line bg-danger-bg px-4 py-3 text-sm text-danger-ink">
              {run.message}
            </p>
          )}
          {r && (
            <>
              <div className="flex flex-col gap-2">
                <h2 className="flex items-center gap-1.5 text-[15px] font-semibold">
                  <Footprints size={16} /> {t("onb_read_first")}
                </h2>
                <p className="text-[13px] text-ink-2">{t("onb_read_first_hint")}</p>
                {!r.has_overview && <p className="text-xs text-muted">{t("onb_no_docs")}</p>}
                <ol className="flex flex-col gap-2.5">
                  {r.files.map((f, i) => (
                    <li key={f.file_path} className="flex gap-3 rounded-lg border border-line px-3 py-2.5">
                      <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-accent text-xs font-semibold text-accent-on">{i + 1}</span>
                      <div className="flex min-w-0 flex-col gap-1 text-sm">
                        <div className="flex flex-wrap items-baseline gap-x-2">
                          <Link to={`/docs?file=${encodeURIComponent(f.file_path)}`} className="break-all font-mono text-[13px] font-semibold hover:underline">
                            {f.file_path}
                          </Link>
                          <span className="text-xs text-muted">{t("onb_used_by", { n: f.callers })}</span>
                        </div>
                        {f.purpose && <p className="text-[13px] text-ink-2">{f.purpose}</p>}
                        {f.key_functions.length > 0 && (
                          <p className="text-xs text-muted">
                            {t("onb_key_functions")} <span className="font-mono text-ink-2">{f.key_functions.map((k) => k.name).join(", ")}</span>
                          </p>
                        )}
                        {f.experts.length > 0 && (
                          <p className="flex flex-wrap items-center gap-1.5 text-xs text-muted">
                            <Users size={12} /> {t("onb_ask_to")}
                            {f.experts.map((e) => (
                              <span key={e.name} className="inline-flex items-center gap-1 text-ink-2">
                                <Avatar name={e.name} size={16} /> {e.name}
                              </span>
                            ))}
                          </p>
                        )}
                      </div>
                    </li>
                  ))}
                </ol>
              </div>
              {r.glossary && (
                <div className="flex flex-col gap-2">
                  <h2 className="flex items-center gap-1.5 text-[15px] font-semibold">
                    <BookOpen size={16} /> {t("onb_glossary")}
                  </h2>
                  <div className="rounded-xl border border-line bg-surface px-4 py-3">
                    <MarkdownViewer content={r.glossary} />
                  </div>
                </div>
              )}
              {mode === "team" && (
                <div className="flex flex-col gap-2">
                  <h2 className="flex items-center gap-1.5 text-[15px] font-semibold">
                    <ListChecks size={16} /> {t("onb_first_issues")}
                  </h2>
                  <p className="text-[13px] text-ink-2">{t("onb_first_issues_hint")}</p>
                  {r.issues.length === 0 ? (
                    <p className="text-sm text-muted">{t("onb_no_issues")}</p>
                  ) : (
                    <ul className="flex flex-col gap-2">
                      {r.issues.map((i) => (
                        <li key={i.id} className="flex flex-col gap-1 rounded-lg border border-line px-3 py-2 text-sm">
                          <div className="flex flex-wrap items-center gap-2">
                            <span className={`rounded-full px-2 py-px text-[11px] font-semibold ${RADIUS_CLASS[i.radius]}`}>{t(`onb_radius_${i.radius}`)}</span>
                            <a href={i.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 hover:underline">
                              <span className="font-mono text-muted">{i.ref}</span> {i.title} <ExternalLink size={12} />
                            </a>
                          </div>
                          <p className="text-xs text-muted">
                            {i.callers !== null && t("onb_callers", { n: i.callers })}
                            {i.functions.length > 0 && <> · <span className="font-mono">{i.functions.join(", ")}</span></>}
                            {i.easy_label && <> · {t("onb_easy_label")}</>}
                          </p>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              )}
            </>
          )}
        </section>
      )}
    </div>
  );
}
