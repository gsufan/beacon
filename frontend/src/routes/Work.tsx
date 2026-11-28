import { CalendarCheck, ClipboardCheck, Copy, ExternalLink, ListChecks, MessageSquarePlus, Send, Sparkles, UserRoundCog, Users } from "lucide-react";
import FollowedPanel from "../components/FollowedPanel";
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router";
import { useAuth } from "../components/AuthProvider";
import { Avatar, LabelChip } from "../components/Colorful";
import { useLanguage } from "../components/LanguageSelector";
import MarkdownViewer from "../components/MarkdownViewer";
import { useProject } from "../components/ProjectSelector";
import IssueTools, { functionTargets, ImpactView } from "../components/IssueTools";
import SourceCitation from "../components/SourceCitation";
import { ApiError } from "../lib/api";
import { hub, type Commit, type Experts, type HubProject, type Impact, type Issue, type IssuesResponse, type Org, type Standup, type StartIssueResponse } from "../lib/hub";
import { inputClass, primaryButton, secondaryButton } from "../lib/ui";

type Load<T> = { status: "loading" } | { status: "ok"; data: T } | { status: "error"; code: number; message: string };
type Filter = "mine" | "all" | number;
type Kind = "start" | "plan" | "qa";
type StandupState = { status: "loading" } | { status: "ok"; data: Standup } | { status: "error"; text: string };
interface StartState {
  issue: Issue;
  kind: Kind;
  result?: StartIssueResponse;
  qa?: { plan: string; impact: Impact };
  error?: string;
}

function relativeDate(iso: string | null, language: string) {
  if (!iso) return "";
  const days = Math.round((Date.now() - new Date(iso).getTime()) / 86400000);
  const rtf = new Intl.RelativeTimeFormat(language, { numeric: "auto" });
  return Math.abs(days) < 1 ? rtf.format(-Math.round((Date.now() - new Date(iso).getTime()) / 3600000), "hour") : rtf.format(-days, "day");
}

function errorText(e: unknown) {
  return e instanceof ApiError ? e.message : String(e);
}

function TrackerLoginForm({ source, onSaved }: { source: "github" | "jira"; onSaved: () => void }) {
  const { t } = useLanguage();
  const { user, refresh } = useAuth();
  const [login, setLogin] = useState("");
  const [error, setError] = useState<string | null>(null);
  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!user) return;
    try {
      await hub.patchUser(user.sub, source === "jira" ? { jira_user: login.trim() } : { github_login: login.trim() });
      await refresh();
      onSaved();
    } catch (err) {
      setError(errorText(err));
    }
  };
  return (
    <form onSubmit={save} className="flex flex-col gap-2 rounded-xl border border-line bg-surface p-4 text-sm">
      <p className="text-ink-2">{source === "jira" ? t("work_need_jira_user") : t("work_need_github_login")}</p>
      <div className="flex gap-2">
        <input
          className={inputClass}
          placeholder={source === "jira" ? t("work_jira_user_placeholder") : t("work_github_login_placeholder")}
          value={login}
          onChange={(e) => setLogin(e.target.value)}
          required
        />
        <button type="submit" className={primaryButton}>
          {t("settings_save")}
        </button>
      </div>
      {error && <p className="text-danger-ink">{error}</p>}
    </form>
  );
}

function IssueCard({ issue, active, onStart }: { issue: Issue; active: boolean; onStart: (kind: Kind) => void }) {
  const { t, language } = useLanguage();
  return (
    <li
      className={`flex flex-col gap-2 rounded-xl border px-4 py-3 shadow-sm transition-colors ${active ? "border-accent bg-accent-tint" : "border-line bg-surface hover:border-line-strong"}`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 flex-col gap-1">
          <span className="text-[14px] font-semibold leading-snug">
            <span className="mr-1.5 font-mono text-[13px] text-muted">{issue.ref}</span>
            {issue.title}
          </span>
          <span className="flex flex-wrap items-center gap-1.5 text-xs text-muted">
            {issue.assignees.map((a) => (
              <Avatar key={a} name={a} size={18} />
            ))}
            {issue.assignees.length > 0 ? issue.assignees.join(", ") : t("work_unassigned")}
            {issue.state && issue.source === "jira" && <> · {issue.state}</>} · {relativeDate(issue.updated_at, language)}
          </span>
        </div>
        <a href={issue.url} target="_blank" rel="noreferrer" className="shrink-0 text-muted hover:text-ink" aria-label={t("work_open_issue")}>
          <ExternalLink size={15} />
        </a>
      </div>
      {issue.labels.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {issue.labels.map((l) => (
            <LabelChip key={l} text={l} />
          ))}
        </div>
      )}
      <div className="flex flex-wrap gap-1.5">
        <button type="button" onClick={() => onStart("start")} className={`${secondaryButton} flex w-fit items-center gap-1.5`}>
          <Sparkles size={14} /> {t("work_where_to_start")}
        </button>
        <button type="button" onClick={() => onStart("plan")} className={`${secondaryButton} flex w-fit items-center gap-1.5`}>
          <ListChecks size={14} /> {t("work_plan")}
        </button>
        <button type="button" onClick={() => onStart("qa")} className={`${secondaryButton} flex w-fit items-center gap-1.5`}>
          <ClipboardCheck size={14} /> {t("work_qa")}
        </button>
      </div>
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
          <Avatar name={c.author} size={24} />
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

function ExpertsBox({ projectId, result }: { projectId: string; result: StartIssueResponse }) {
  const { t } = useLanguage();
  const [data, setData] = useState<Experts | null>(null);
  useEffect(() => {
    const paths = [...new Set(result.sources.filter((s) => s.chunk_type !== "overview").map((s) => s.file_path))].slice(0, 6);
    if (paths.length === 0) return;
    hub.experts(projectId, paths).then(setData, () => setData(null));
  }, [projectId, result]);
  if (!data || data.overall.length === 0) return null;
  return (
    <div className="flex flex-col gap-2 rounded-xl border border-line bg-surface px-4 py-3 text-sm">
      <span className="flex items-center gap-1.5 text-[13px] font-semibold text-muted">
        <Users size={14} /> {t("work_experts")}
      </span>
      <ul className="flex flex-col gap-1.5">
        {data.overall.map((a) => (
          <li key={a.name} className="flex items-center gap-2">
            <Avatar name={a.name} size={22} />
            <span className="font-medium">{a.name}</span>
            <span className="text-xs text-muted">{t("work_experts_commits", { n: a.commits })}</span>
          </li>
        ))}
      </ul>
      <p className="text-xs text-muted">{t("work_experts_hint")}</p>
    </div>
  );
}

function shareText(project: string, issue: Issue, result: StartIssueResponse) {
  const files = [...new Set(result.sources.filter((s) => s.chunk_type !== "overview").map((s) => `${s.file_path} (${s.start_line}-${s.end_line})`))].slice(0, 8);
  return `${issue.ref} ${issue.title}\n\n${result.answer.slice(0, 2500)}\n\nFuentes:\n${files.map((f) => `- ${f}`).join("\n")}\n\n(${project})`;
}

function StartPanel({
  projectId,
  projectName,
  org,
  state,
}: {
  projectId: string;
  projectName: string;
  org: Org | null;
  state: StartState | null;
}) {
  const { t } = useLanguage();
  const [action, setAction] = useState<{ ok: boolean; text: string; url?: string } | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => setAction(null), [state?.issue.id, state?.result, state?.kind]);
  if (!state) {
    return (
      <div className="flex flex-col gap-2 rounded-2xl border border-line bg-gradient-to-br from-[var(--c5-tint)] to-[var(--c2-tint)] p-5 text-sm text-ink-2">
        <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-gradient-to-br from-[var(--c5)] to-[var(--c2)] text-white">
          <Sparkles size={18} />
        </span>
        <p className="font-semibold text-ink">{t("work_start_empty_title")}</p>
        <p>{t("work_start_empty_body")}</p>
      </div>
    );
  }
  const run = async (fn: () => Promise<{ url?: string } | unknown>, ok: string) => {
    setBusy(true);
    setAction(null);
    try {
      const res = (await fn()) as { url?: string };
      setAction({ ok: true, text: ok, url: res?.url });
    } catch (e) {
      setAction({ ok: false, text: errorText(e) });
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="flex flex-col gap-3" aria-busy={!state.result && !state.qa && !state.error}>
      <div className="flex flex-col gap-0.5 border-b border-line pb-3">
        <span className="text-xs font-semibold text-muted">
          {state.kind === "plan" ? t("work_plan") : state.kind === "qa" ? t("work_qa") : t("work_where_to_start")}
        </span>
        <span className="text-[15px] font-semibold">
          <span className="mr-1.5 font-mono text-muted">{state.issue.ref}</span>
          {state.issue.title}
        </span>
      </div>
      {!state.result && !state.qa && !state.error && (
        <p className="text-sm text-ink-2">{state.kind === "qa" ? t("work_qa_loading") : t("work_start_loading")}</p>
      )}
      {state.qa && (
        <>
          <div className="rounded-xl border border-line bg-surface px-4 py-3">
            <MarkdownViewer content={state.qa.plan} />
          </div>
          <div className="flex flex-col gap-2 rounded-xl border border-line bg-surface px-4 py-3">
            <span className="text-[13px] font-semibold text-muted">{t("impact_title")}</span>
            <ImpactView impact={state.qa.impact} />
          </div>
        </>
      )}
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
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              disabled={busy}
              className={`${secondaryButton} flex items-center gap-1.5`}
              onClick={() =>
                window.confirm(t("work_comment_confirm", { ref: state.issue.ref })) &&
                run(() => hub.comment(projectId, state.issue.id, shareText(projectName, state.issue, state.result!)), t("work_comment_done"))
              }
            >
              <MessageSquarePlus size={14} /> {t("work_comment")}
            </button>
            {org?.slack_connected && (
              <button
                type="button"
                disabled={busy}
                className={`${secondaryButton} flex items-center gap-1.5`}
                onClick={() =>
                  window.confirm(t("work_slack_confirm")) &&
                  run(() => hub.shareSlack(projectId, shareText(projectName, state.issue, state.result!)), t("work_slack_done"))
                }
              >
                <Send size={14} /> {t("work_slack")}
              </button>
            )}
          </div>
          {action && (
            <p role={action.ok ? "status" : "alert"} className={`text-sm ${action.ok ? "text-ok" : "text-danger-ink"}`}>
              {action.text}{" "}
              {action.url && (
                <a href={action.url} target="_blank" rel="noreferrer" className="underline">
                  {t("work_open_comment")}
                </a>
              )}
            </p>
          )}
          <ExpertsBox projectId={projectId} result={state.result} />
          <IssueTools projectId={projectId} issue={state.issue} sources={state.result.sources} />
          <h3 className="text-[13px] font-semibold text-muted">{t("sources_count", { n: state.result.sources.length })}</h3>
          <div className="flex flex-col gap-2">
            {state.result.sources.map((s, i) => (
              <SourceCitation key={`${s.file_path}-${s.start_line}-${i}`} source={s} showWhy />
            ))}
          </div>
        </>
      )}
    </div>
  );
}

function StandupPanel({ state, projectId, org }: { state: StandupState; projectId: string; org: Org | null }) {
  const { t } = useLanguage();
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  if (state.status === "loading") return <p className="rounded-xl border border-line bg-surface px-4 py-3 text-sm text-ink-2">{t("standup_loading")}</p>;
  if (state.status === "error")
    return (
      <p role="alert" className="rounded-xl border border-danger-line bg-danger-bg px-4 py-3 text-sm text-danger-ink">
        {state.text}
      </p>
    );
  const { data } = state;
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(data.draft);
      setNote({ ok: true, text: t("standup_copied") });
    } catch (e) {
      setNote({ ok: false, text: errorText(e) });
    }
  };
  const share = async () => {
    if (!window.confirm(t("work_slack_confirm"))) return;
    try {
      await hub.shareSlack(projectId, data.draft);
      setNote({ ok: true, text: t("work_slack_done") });
    } catch (e) {
      setNote({ ok: false, text: errorText(e) });
    }
  };
  return (
    <div className="flex flex-col gap-3">
      <div className="rounded-xl border border-line bg-surface px-4 py-3 text-sm">
        <p className="mb-1 text-[13px] font-semibold text-muted">{t("standup_title")}</p>
        <MarkdownViewer content={data.draft} />
      </div>
      <p className="text-xs text-muted">{t("standup_since", { date: data.since, local: data.local_commits.length, remote: data.remote_commits })}</p>
      <div className="flex flex-wrap gap-2">
        <button type="button" className={`${secondaryButton} flex items-center gap-1.5`} onClick={copy}>
          <Copy size={14} /> {t("standup_copy")}
        </button>
        {org?.slack_connected && (
          <button type="button" className={`${secondaryButton} flex items-center gap-1.5`} onClick={share}>
            <Send size={14} /> {t("work_slack")}
          </button>
        )}
      </div>
      {note && <p className={`text-sm ${note.ok ? "text-ok" : "text-danger-ink"}`}>{note.text}</p>}
    </div>
  );
}

export default function Work() {
  const { t, language } = useLanguage();
  const { user, isAdmin, roleIn } = useAuth();
  const { projectId, projects } = useProject();
  const [filter, setFilter] = useState<Filter>("mine");
  const [issues, setIssues] = useState<Load<IssuesResponse>>({ status: "loading" });
  const [activity, setActivity] = useState<Load<Commit[]>>({ status: "loading" });
  const [start, setStart] = useState<StartState | null>(null);
  const [handover, setHandover] = useState<{ status: "loading" | "ok" | "error"; text: string } | null>(null);
  const [standup, setStandup] = useState<StandupState | null>(null);
  const [org, setOrg] = useState<Org | null>(null);
  const [team, setTeam] = useState<HubProject | null>(null);
  const project = projects.find((p) => p.id === projectId);
  const role = roleIn(projectId);

  useEffect(() => {
    hub.org().then(setOrg, () => setOrg(null));
  }, []);

  useEffect(() => {
    setFilter("mine");
    if (!projectId) return;
    hub.projects().then((r) => setTeam(r.projects.find((p) => p.id === projectId) ?? null), () => setTeam(null));
  }, [projectId]);

  const other = typeof filter === "number" ? team?.members.find((m) => m.id === filter) : undefined;

  const load = useCallback(() => {
    if (!projectId) return;
    const fail = (e: unknown) => ({ status: "error" as const, code: e instanceof ApiError ? e.status : 0, message: errorText(e) });
    setIssues({ status: "loading" });
    setActivity({ status: "loading" });
    hub.issues(projectId, filter).then((d) => setIssues({ status: "ok", data: d }), (e) => setIssues(fail(e)));
    hub.activity(projectId, typeof filter === "number" ? filter : undefined).then(
      (d) => setActivity({ status: "ok", data: d.commits }),
      (e) => setActivity(fail(e)),
    );
  }, [projectId, filter]);

  useEffect(() => {
    setStart(null);
    setHandover(null);
    setStandup(null);
    load();
  }, [load]);

  const startIssue = async (issue: Issue, kind: Kind) => {
    if (!projectId) return;
    setHandover(null);
    setStandup(null);
    setStart({ issue, kind });
    try {
      if (kind === "plan") {
        setStart({ issue, kind, result: await hub.planIssue(projectId, issue.id, language) });
      } else if (kind === "qa") {
        const base = start?.issue.id === issue.id && start.result ? start.result : await hub.startIssue(projectId, issue.id, language);
        const targets = functionTargets(base.sources);
        if (targets.length === 0) throw new Error(t("work_qa_no_targets"));
        setStart({ issue, kind, qa: await hub.qaPlan(projectId, targets, issue, language) });
      } else {
        setStart({ issue, kind, result: await hub.startIssue(projectId, issue.id, language) });
      }
    } catch (e) {
      setStart({ issue, kind, error: errorText(e) });
    }
  };

  const runHandover = async () => {
    if (!projectId || !other) return;
    setStart(null);
    setHandover({ status: "loading", text: "" });
    try {
      const res = await hub.handover(projectId, {
        person: other.name,
        git_author: other.github_login ? `${other.name}\\|${other.github_login}` : other.name,
        issues: issues.status === "ok" ? issues.data.issues : [],
        commits: activity.status === "ok" ? activity.data : [],
        language,
      });
      setHandover({ status: "ok", text: res.summary });
    } catch (e) {
      setHandover({ status: "error", text: errorText(e) });
    }
  };

  const runStandup = async () => {
    if (!projectId || !user) return;
    setStart(null);
    setHandover(null);
    setStandup({ status: "loading" });
    try {
      const data = await hub.standup(projectId, {
        person: user.name,
        git_author: user.github ? `${user.name}\\|${user.github}` : user.name,
        issues: issues.status === "ok" ? issues.data.issues : [],
        // Without a GitHub login the activity shows the whole team: only the person's own commits count.
        commits: activity.status === "ok" && user.github ? activity.data.filter((c) => c.author.toLowerCase() === user.github!.toLowerCase()) : [],
        language,
      });
      setStandup({ status: "ok", data });
    } catch (e) {
      setStandup({ status: "error", text: errorText(e) });
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
  const data = issues.status === "ok" ? issues.data : null;
  const backlog = data ? (data.source === "jira" ? `Jira · ${data.where}` : `github.com/${data.where}`) : null;
  const others = (team?.members ?? []).filter((m) => m.id !== user?.sub);

  return (
    <div className="flex flex-col gap-6 px-10 pb-10 pt-6 lg:flex-row lg:items-start">
      <div className="flex min-w-0 flex-1 flex-col gap-6">
        <div className="flex flex-col gap-2 rounded-2xl bg-gradient-to-r from-[var(--hero-from)] to-[var(--hero-to)] px-6 py-5 text-white shadow-sm">
          <h1 className="font-display text-[24px] font-semibold">{t("work_greeting", { name: user?.name.split(" ")[0] ?? "" })}</h1>
          <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[13px] text-white/85">
            <span className="font-semibold text-white">{project.name}</span>
            {role && <span className="rounded-full bg-white/20 px-2 py-px text-[11px] font-semibold text-white">{roleLabel}</span>}
            {backlog && <span className="font-mono text-white/80">{backlog}</span>}
          </p>
        </div>

        <section className="flex flex-col gap-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-[17px] font-semibold">{t("work_issues")}</h2>
            <div className="flex flex-wrap items-center gap-2">
              <div role="tablist" className="flex gap-1 rounded-lg bg-surface-2 p-1">
                {(["mine", "all"] as const).map((f) => (
                  <button
                    key={f}
                    role="tab"
                    type="button"
                    aria-selected={filter === f}
                    onClick={() => setFilter(f)}
                    className={`h-7 whitespace-nowrap rounded-md px-3 text-[13px] ${filter === f ? "bg-surface font-semibold text-accent-ink" : "text-ink-2"}`}
                  >
                    {f === "mine" ? t("work_mine") : t("work_all_open")}
                  </button>
                ))}
              </div>
              {filter === "mine" && (
                <button
                  type="button"
                  className={`${secondaryButton} flex items-center gap-1.5`}
                  disabled={standup?.status === "loading" || issues.status === "loading" || activity.status === "loading"}
                  onClick={runStandup}
                >
                  <CalendarCheck size={14} /> {t("standup_run")}
                </button>
              )}
              {others.length > 0 && (
                <select
                  aria-label={t("work_of_person")}
                  className="h-8 rounded-lg border border-line-strong bg-surface px-2 text-[13px]"
                  value={typeof filter === "number" ? String(filter) : ""}
                  onChange={(e) => setFilter(e.target.value ? Number(e.target.value) : "mine")}
                >
                  <option value="">{t("work_of_person")}</option>
                  {others.map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.name}
                    </option>
                  ))}
                </select>
              )}
            </div>
          </div>
          {other && (
            <div className="flex flex-col gap-2 rounded-lg bg-accent-tint px-3 py-2 text-[13px] text-accent-ink">
              <p>{t("work_viewing_person", { name: other.name })}</p>
              <button type="button" className={`${secondaryButton} flex w-fit items-center gap-1.5`} disabled={handover?.status === "loading"} onClick={runHandover}>
                <UserRoundCog size={14} /> {handover?.status === "loading" ? t("work_loading") : t("handover_run")}
              </button>
            </div>
          )}
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
          {data?.needs_login && filter === "mine" && <TrackerLoginForm source={data.source} onSaved={load} />}
          {data?.needs_login && typeof filter === "number" && (
            <p className="text-sm text-muted">{t("work_person_no_login", { name: data.person ?? "" })}</p>
          )}
          {data && !data.needs_login && data.issues.length === 0 && (
            <p className="text-sm text-muted">
              {filter === "all" ? t("work_no_issues") : other ? t("work_no_issues_person", { name: other.name }) : t("work_no_issues_mine")}
            </p>
          )}
          {data && data.issues.length > 0 && (
            <ul className="flex flex-col gap-2">
              {data.issues.map((i) => (
                <IssueCard key={i.id} issue={i} active={start?.issue.id === i.id} onStart={(kind) => startIssue(i, kind)} />
              ))}
            </ul>
          )}
        </section>

        <section className="flex flex-col gap-2">
          <h2 className="text-[17px] font-semibold">{other ? t("work_activity_of", { name: other.name }) : t("work_activity")}</h2>
          <p className="text-[13px] text-ink-2">{t("work_activity_hint")}</p>
          {activity.status === "loading" && <p className="text-sm text-muted">{t("work_loading")}</p>}
          {activity.status === "error" && activity.code === 409 && <p className="text-sm text-muted">{t("work_activity_github_only")}</p>}
          {activity.status === "error" && activity.code !== 404 && activity.code !== 409 && (
            <p className="text-sm text-danger-ink">{activity.message}</p>
          )}
          {activity.status === "ok" && <ActivityList commits={activity.data} />}
        </section>
      </div>
      <aside className="flex w-full shrink-0 flex-col gap-4 lg:sticky lg:top-6 lg:max-h-[calc(100vh-3rem)] lg:w-[440px] lg:overflow-y-auto lg:pr-1">
        <FollowedPanel projectId={projectId} />
        {standup && !start ? (
          <StandupPanel state={standup} projectId={projectId} org={org} />
        ) : handover && handover.status !== "loading" && !start ? (
          <div className={`rounded-xl border px-4 py-3 text-sm ${handover.status === "ok" ? "border-line bg-surface" : "border-danger-line bg-danger-bg text-danger-ink"}`}>
            {handover.status === "ok" ? (
              <>
                <p className="mb-1 text-[13px] font-semibold text-muted">{t("handover_title", { name: other?.name ?? "" })}</p>
                <MarkdownViewer content={handover.text} />
              </>
          ) : (
            handover.text
          )}
        </div>
        ) : (
          <StartPanel projectId={projectId} projectName={project.name} org={org} state={start} />
        )}
      </aside>
    </div>
  );
}
