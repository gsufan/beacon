import { ExternalLink, GitCommit, Sparkles } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router";
import { useAuth } from "../components/AuthProvider";
import { useLanguage } from "../components/LanguageSelector";
import MarkdownViewer from "../components/MarkdownViewer";
import { useProject } from "../components/ProjectSelector";
import SourceCitation from "../components/SourceCitation";
import { ApiError } from "../lib/api";
import { hub, type Commit, type Issue, type StartIssueResponse } from "../lib/hub";
import { inputClass, primaryButton, secondaryButton } from "../lib/ui";

type Load<T> = { status: "loading" } | { status: "ok"; data: T } | { status: "error"; code: number; message: string };

function relativeDate(iso: string | null, language: string) {
  if (!iso) return "";
  const days = Math.round((Date.now() - new Date(iso).getTime()) / 86400000);
  const rtf = new Intl.RelativeTimeFormat(language, { numeric: "auto" });
  return Math.abs(days) < 1 ? rtf.format(-Math.round((Date.now() - new Date(iso).getTime()) / 3600000), "hour") : rtf.format(-days, "day");
}

function GitHubLoginForm({ onSaved }: { onSaved: () => void }) {
  const { t } = useLanguage();
  const { user, refresh } = useAuth();
  const [login, setLogin] = useState("");
  const [error, setError] = useState<string | null>(null);
  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!user) return;
    try {
      await hub.patchUser(user.sub, { github_login: login.trim() });
      await refresh();
      onSaved();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  };
  return (
    <form onSubmit={save} className="flex flex-col gap-2 rounded-xl border border-line bg-surface p-4 text-sm">
      <p className="text-ink-2">{t("work_need_github_login")}</p>
      <div className="flex gap-2">
        <input className={inputClass} placeholder={t("work_github_login_placeholder")} value={login} onChange={(e) => setLogin(e.target.value)} required />
        <button type="submit" className={primaryButton}>
          {t("settings_save")}
        </button>
      </div>
      {error && <p className="text-danger-ink">{error}</p>}
    </form>
  );
}

function IssueCard({ issue, active, onStart }: { issue: Issue; active: boolean; onStart: () => void }) {
  const { t, language } = useLanguage();
  return (
    <li
      className={`flex flex-col gap-2 rounded-xl border px-4 py-3 ${active ? "border-accent bg-accent-tint" : "border-line bg-surface"}`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 flex-col gap-1">
          <span className="text-[14px] font-semibold leading-snug">
            <span className="mr-1.5 font-mono text-[13px] text-muted">#{issue.number}</span>
            {issue.title}
          </span>
          <span className="text-xs text-muted">
            {issue.assignees.length > 0 ? issue.assignees.join(", ") : t("work_unassigned")} · {relativeDate(issue.updated_at, language)}
          </span>
        </div>
        <a href={issue.url} target="_blank" rel="noreferrer" className="shrink-0 text-muted hover:text-ink" aria-label={t("work_open_github")}>
          <ExternalLink size={15} />
        </a>
      </div>
      {issue.labels.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {issue.labels.map((l) => (
            <span key={l} className="rounded-full bg-surface-2 px-2 py-px text-[11px] text-ink-2">
              {l}
            </span>
          ))}
        </div>
      )}
      <button type="button" onClick={onStart} className={`${secondaryButton} flex w-fit items-center gap-1.5`}>
        <Sparkles size={14} /> {t("work_where_to_start")}
      </button>
    </li>
  );
}

function ActivityList({ commits }: { commits: Commit[] }) {
  const { t, language } = useLanguage();
  if (commits.length === 0) return <p className="text-sm text-muted">{t("work_no_activity")}</p>;
  return (
    <ul className="flex flex-col">
      {commits.map((c) => (
        <li key={c.sha} className="flex gap-3 border-b border-line py-2.5 last:border-0">
          <GitCommit size={16} className="mt-0.5 shrink-0 text-muted" />
          <div className="flex min-w-0 flex-col gap-0.5 text-sm">
            <a href={c.url} target="_blank" rel="noreferrer" className="break-words hover:underline">
              {c.message}
            </a>
            <span className="text-xs text-muted">
              {c.author} · {relativeDate(c.date, language)} · <span className="font-mono">{c.sha}</span>
              {c.issues.length > 0 && <> · {c.issues.map((n) => `#${n}`).join(" ")}</>}
            </span>
          </div>
        </li>
      ))}
    </ul>
  );
}

function StartPanel({ state }: { state: { issue: Issue; result?: StartIssueResponse; error?: string } | null }) {
  const { t } = useLanguage();
  if (!state) {
    return (
      <div className="flex flex-col gap-2 rounded-xl border border-dashed border-line-strong p-5 text-sm text-ink-2">
        <Sparkles size={18} className="text-accent-ink" />
        <p className="font-semibold text-ink">{t("work_start_empty_title")}</p>
        <p>{t("work_start_empty_body")}</p>
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-3" aria-busy={!state.result && !state.error}>
      <div className="flex flex-col gap-0.5 border-b border-line pb-3">
        <span className="text-xs font-semibold text-muted">{t("work_where_to_start")}</span>
        <span className="text-[15px] font-semibold">
          <span className="mr-1.5 font-mono text-muted">#{state.issue.number}</span>
          {state.issue.title}
        </span>
      </div>
      {!state.result && !state.error && <p className="text-sm text-ink-2">{t("work_start_loading")}</p>}
      {state.error && (
        <p role="alert" className="rounded-xl border border-danger-line bg-danger-bg px-4 py-3 text-sm text-danger-ink">
          {state.error}
        </p>
      )}
      {state.result && (
        <>
          <div className="rounded-xl border border-line bg-surface px-4 py-3">
            <MarkdownViewer content={state.result.answer} />
          </div>
          <h3 className="text-[13px] font-semibold text-muted">{t("sources_count", { n: state.result.sources.length })}</h3>
          <div className="flex flex-col gap-2">
            {state.result.sources.map((s, i) => (
              <SourceCitation key={`${s.file_path}-${s.start_line}-${i}`} source={s} />
            ))}
          </div>
        </>
      )}
    </div>
  );
}

export default function Work() {
  const { t, language } = useLanguage();
  const { user, isAdmin, roleIn } = useAuth();
  const { projectId, projects } = useProject();
  const [mine, setMine] = useState(true);
  const [issues, setIssues] = useState<Load<{ issues: Issue[]; repo: string; needs_github_login?: boolean }>>({ status: "loading" });
  const [activity, setActivity] = useState<Load<Commit[]>>({ status: "loading" });
  const [start, setStart] = useState<{ issue: Issue; result?: StartIssueResponse; error?: string } | null>(null);
  const project = projects.find((p) => p.id === projectId);
  const role = roleIn(projectId);

  const load = useCallback(() => {
    if (!projectId) return;
    const fail = (e: unknown) => ({
      status: "error" as const,
      code: e instanceof ApiError ? e.status : 0,
      message: e instanceof ApiError ? e.message : String(e),
    });
    setIssues({ status: "loading" });
    setActivity({ status: "loading" });
    hub.issues(projectId, mine).then((d) => setIssues({ status: "ok", data: d }), (e) => setIssues(fail(e)));
    hub.activity(projectId).then((d) => setActivity({ status: "ok", data: d.commits }), (e) => setActivity(fail(e)));
  }, [projectId, mine]);

  useEffect(() => {
    setStart(null);
    load();
  }, [load]);

  const startIssue = async (issue: Issue) => {
    if (!projectId) return;
    setStart({ issue });
    try {
      const result = await hub.startIssue(projectId, issue.number, language);
      setStart({ issue, result });
    } catch (e) {
      setStart({ issue, error: e instanceof ApiError ? e.message : String(e) });
    }
  };

  const roleLabel = role ? t(`role_${role}` as "role_admin") : "";

  if (!projectId || !project) {
    return (
      <div className="mx-auto max-w-2xl px-10 py-10 text-sm text-ink-2">
        <h1 className="mb-2 font-display text-[22px] font-semibold text-ink">{t("nav_work")}</h1>
        <p>{isAdmin ? t("work_no_projects_admin") : t("work_no_projects")}</p>
      </div>
    );
  }

  const issueError = issues.status === "error" ? issues : null;

  return (
    <div className="flex flex-col gap-6 px-10 pb-10 pt-6 lg:flex-row lg:items-start">
      <div className="flex min-w-0 flex-1 flex-col gap-6">
        <div className="flex flex-col gap-1">
          <h1 className="font-display text-[22px] font-semibold">
            {t("work_greeting", { name: user?.name.split(" ")[0] ?? "" })}
          </h1>
          <p className="text-[13px] text-ink-2">
            {project.name}
            {roleLabel && <> · {t("work_your_role", { role: roleLabel })}</>}
            {issues.status === "ok" && <> · GitHub: <span className="font-mono">{issues.data.repo}</span></>}
          </p>
        </div>

        <section className="flex flex-col gap-3">
          <div className="flex items-center justify-between gap-2">
            <h2 className="text-[17px] font-semibold">{t("work_issues")}</h2>
            <div role="tablist" className="flex gap-1 rounded-lg bg-surface-2 p-1">
              {[true, false].map((m) => (
                <button
                  key={String(m)}
                  role="tab"
                  type="button"
                  aria-selected={mine === m}
                  onClick={() => setMine(m)}
                  className={`h-7 rounded-md px-3 text-[13px] ${mine === m ? "bg-surface font-semibold text-accent-ink" : "text-ink-2"}`}
                >
                  {m ? t("work_mine") : t("work_all_open")}
                </button>
              ))}
            </div>
          </div>
          {issues.status === "loading" && <p className="text-sm text-muted">{t("work_loading")}</p>}
          {issueError && issueError.code === 404 && (
            <p className="rounded-xl border border-line bg-surface px-4 py-3 text-sm text-ink-2">{t("work_not_in_team")}</p>
          )}
          {issueError && issueError.code === 409 && (
            <p className="rounded-xl border border-line bg-surface px-4 py-3 text-sm text-ink-2">
              {t("work_no_repo")}{" "}
              {(isAdmin || role === "product_owner") && (
                <Link to="/team" className="font-semibold text-accent-ink hover:underline">
                  {t("work_link_repo")}
                </Link>
              )}
            </p>
          )}
          {issueError && issueError.code !== 404 && issueError.code !== 409 && (
            <p role="alert" className="rounded-xl border border-danger-line bg-danger-bg px-4 py-3 text-sm text-danger-ink">
              {issueError.message}
            </p>
          )}
          {issues.status === "ok" && issues.data.needs_github_login && <GitHubLoginForm onSaved={load} />}
          {issues.status === "ok" && !issues.data.needs_github_login && issues.data.issues.length === 0 && (
            <p className="text-sm text-muted">{mine ? t("work_no_issues_mine") : t("work_no_issues")}</p>
          )}
          {issues.status === "ok" && issues.data.issues.length > 0 && (
            <ul className="flex flex-col gap-2">
              {issues.data.issues.map((i) => (
                <IssueCard key={i.number} issue={i} active={start?.issue.number === i.number} onStart={() => startIssue(i)} />
              ))}
            </ul>
          )}
        </section>

        <section className="flex flex-col gap-2">
          <h2 className="text-[17px] font-semibold">{t("work_activity")}</h2>
          <p className="text-[13px] text-ink-2">{t("work_activity_hint")}</p>
          {activity.status === "loading" && <p className="text-sm text-muted">{t("work_loading")}</p>}
          {activity.status === "error" && activity.code !== 404 && activity.code !== 409 && (
            <p className="text-sm text-danger-ink">{activity.message}</p>
          )}
          {activity.status === "ok" && <ActivityList commits={activity.data} />}
        </section>
      </div>
      <aside className="w-full shrink-0 lg:sticky lg:top-6 lg:w-[440px]">
        <StartPanel state={start} />
      </aside>
    </div>
  );
}
