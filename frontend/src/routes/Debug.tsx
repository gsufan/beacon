import { Bug, Copy, GitCommit, GitPullRequest, History, Users } from "lucide-react";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router";
import { Avatar } from "../components/Colorful";
import { useLanguage } from "../components/LanguageSelector";
import MarkdownViewer from "../components/MarkdownViewer";
import { useProject } from "../components/ProjectSelector";
import SourceCitation from "../components/SourceCitation";
import { ApiError } from "../lib/api";
import { hub, type CodeHistory, type ErrorExplanation, type PreReview } from "../lib/hub";
import { cardClass, inputClass, primaryButton, secondaryButton } from "../lib/ui";

type Tab = "error" | "why" | "pr";
type Run<T> = { status: "idle" } | { status: "loading" } | { status: "ok"; data: T } | { status: "error"; message: string };

const errorText = (e: unknown) => (e instanceof ApiError ? e.message : String(e));

function Experts({ experts, label }: { experts: { name: string; commits: number }[]; label?: string }) {
  const { t } = useLanguage();
  if (experts.length === 0) return null;
  return (
    <p className="flex flex-wrap items-center gap-1.5 text-xs text-ink-2">
      <Users size={12} /> {label ?? t("debug_experts")}
      {experts.map((e) => (
        <span key={e.name} className="inline-flex items-center gap-1">
          <Avatar name={e.name} size={16} /> {e.name}
        </span>
      ))}
    </p>
  );
}

function ErrorTab({ projectId }: { projectId: string }) {
  const { t, language } = useLanguage();
  const [trace, setTrace] = useState("");
  const [run, setRun] = useState<Run<ErrorExplanation>>({ status: "idle" });
  useEffect(() => setRun({ status: "idle" }), [projectId]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setRun({ status: "loading" });
    try {
      setRun({ status: "ok", data: await hub.explainError(projectId, trace, language) });
    } catch (err) {
      setRun({ status: "error", message: errorText(err) });
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <form onSubmit={submit} className="flex flex-col gap-2">
        <label htmlFor="trace" className="text-[13px] font-semibold text-muted">
          {t("debug_error_label")}
        </label>
        <textarea
          id="trace"
          value={trace}
          onChange={(e) => setTrace(e.target.value)}
          placeholder={t("debug_error_placeholder")}
          rows={9}
          spellCheck={false}
          className="w-full rounded-lg border border-line-strong bg-surface px-3 py-2 font-mono text-xs outline-none placeholder:text-muted focus:border-accent"
        />
        <button type="submit" className={primaryButton} disabled={trace.trim().length < 3 || run.status === "loading"}>
          {run.status === "loading" ? t("work_loading") : t("debug_error_run")}
        </button>
      </form>
      {run.status === "loading" && <p className="text-sm text-ink-2">{t("debug_error_loading")}</p>}
      {run.status === "error" && (
        <p role="alert" className="rounded-xl border border-danger-line bg-danger-bg px-4 py-3 text-sm text-danger-ink">
          {run.message}
        </p>
      )}
      {run.status === "ok" && (
        <div className="flex flex-col gap-4">
          {run.data.message && <p className="font-mono text-[13px] font-semibold text-danger-ink">{run.data.message}</p>}
          {!run.data.in_project && <p className="text-sm text-ink-2">{t("debug_not_in_project")}</p>}
          <div className="rounded-xl border border-line bg-surface px-4 py-3">
            <MarkdownViewer content={run.data.explanation} />
          </div>
          <Experts experts={run.data.experts} />
          {run.data.frames.length > 0 && (
            <div className="flex flex-col gap-1.5">
              <h3 className="text-[13px] font-semibold text-muted">{t("debug_frames")}</h3>
              <ol className="flex flex-col gap-1 font-mono text-xs">
                {run.data.frames.map((f, i) => (
                  <li key={`${f.path}-${f.line}-${i}`} className={f.file_path ? "text-ink" : "text-muted"}>
                    {f.file_path ?? f.path}:{f.line}
                    {(f.name || f.func) && <> · {f.name || f.func}</>}
                    {!f.file_path && <span className="ml-1.5 font-sans">({t("debug_frame_outside")})</span>}
                  </li>
                ))}
              </ol>
            </div>
          )}
          {run.data.sources.length > 0 && (
            <div className="flex flex-col gap-2">
              <h3 className="text-[13px] font-semibold text-muted">{t("sources_count", { n: run.data.sources.length })}</h3>
              {run.data.sources.map((s, i) => (
                <SourceCitation key={`${s.file_path}-${s.start_line}-${i}`} source={s} defaultOpen={i === 0} showWhy />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function WhyTab({ projectId }: { projectId: string }) {
  const { t, language } = useLanguage();
  const [params] = useSearchParams();
  const [name, setName] = useState(params.get("name") ?? "");
  const [file, setFile] = useState(params.get("file") ?? "");
  const [start, setStart] = useState(params.get("start") ?? "");
  const [end, setEnd] = useState(params.get("end") ?? "");
  const [run, setRun] = useState<Run<CodeHistory>>({ status: "idle" });

  const ask = async () => {
    setRun({ status: "loading" });
    try {
      const body = name.trim()
        ? { name: name.trim(), file_path: file.trim() || undefined, language }
        : { file_path: file.trim(), start_line: Number(start), end_line: Number(end || start), language };
      setRun({ status: "ok", data: await hub.why(projectId, body) });
    } catch (err) {
      setRun({ status: "error", message: errorText(err) });
    }
  };

  // Opened from a source ("Why is it like this?"): ask right away.
  useEffect(() => {
    if (params.get("file") || params.get("name")) void ask();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  const canAsk = name.trim().length > 0 || (file.trim().length > 0 && Number(start) > 0);
  const h = run.status === "ok" ? run.data : null;

  return (
    <div className="flex flex-col gap-4">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void ask();
        }}
        className="flex flex-col gap-3"
      >
        <label className="flex flex-col gap-1 text-[13px] font-semibold text-muted">
          {t("debug_why_name")}
          <input className={`${inputClass} font-mono`} value={name} onChange={(e) => setName(e.target.value)} placeholder={t("debug_why_name_placeholder")} />
        </label>
        <p className="text-xs text-muted">{t("debug_why_or")}</p>
        <div className="flex flex-wrap gap-2">
          <label className="flex min-w-[240px] flex-1 flex-col gap-1 text-[13px] font-semibold text-muted">
            {t("debug_why_file")}
            <input className={`${inputClass} font-mono`} value={file} onChange={(e) => setFile(e.target.value)} placeholder="src/requests/sessions.py" />
          </label>
          <label className="flex w-24 flex-col gap-1 text-[13px] font-semibold text-muted">
            {t("debug_why_start")}
            <input className={inputClass} type="number" min={1} value={start} onChange={(e) => setStart(e.target.value)} />
          </label>
          <label className="flex w-24 flex-col gap-1 text-[13px] font-semibold text-muted">
            {t("debug_why_end")}
            <input className={inputClass} type="number" min={1} value={end} onChange={(e) => setEnd(e.target.value)} />
          </label>
        </div>
        <button type="submit" className={primaryButton} disabled={!canAsk || run.status === "loading"}>
          {run.status === "loading" ? t("work_loading") : t("debug_why_run")}
        </button>
      </form>
      {run.status === "loading" && <p className="text-sm text-ink-2">{t("debug_why_loading")}</p>}
      {run.status === "error" && (
        <p role="alert" className="rounded-xl border border-danger-line bg-danger-bg px-4 py-3 text-sm text-danger-ink">
          {run.message}
        </p>
      )}
      {h && (
        <div className="flex flex-col gap-4">
          <p className="font-mono text-[13px] text-ink-2">
            {h.name && <span className="font-semibold text-ink">{h.name} · </span>}
            {h.file_path} ({h.start_line}-{h.end_line})
          </p>
          <div className="rounded-xl border border-line bg-surface px-4 py-3">
            <MarkdownViewer content={h.explanation} />
          </div>
          <div className="flex flex-col gap-1.5">
            <h3 className="flex items-center gap-1.5 text-[13px] font-semibold text-muted">
              <GitCommit size={14} /> {t("debug_why_commits")}
            </h3>
            <ul className="flex flex-col">
              {h.commits.map((c) => (
                <li key={c.sha} className="flex gap-3 border-b border-line py-2 last:border-0">
                  {c.author && <Avatar name={c.author} size={22} />}
                  <div className="flex min-w-0 flex-col gap-0.5 text-sm">
                    <span className="break-words">{c.subject}</span>
                    <span className="text-xs text-muted">
                      {c.author} · {c.date} · <span className="font-mono">{c.sha}</span> · {t("debug_why_lines", { n: c.lines })}
                      {c.issues.length > 0 && <> · {c.issues.join(" ")}</>}
                    </span>
                  </div>
                </li>
              ))}
            </ul>
            {h.created && (
              <p className="text-xs text-ink-2">{t("debug_why_created", { sha: h.created.sha, date: h.created.date, author: h.created.author })}</p>
            )}
            {h.shallow && <p className="text-xs text-muted">{t("debug_why_shallow")}</p>}
            {h.other_definitions.length > 0 && (
              <p className="text-xs text-muted">
                {t("debug_why_others", { files: h.other_definitions.map((o) => `${o.file_path}:${o.start_line}`).join(", ") })}
              </p>
            )}
          </div>
          {h.issues.length > 0 && (
            <div className="flex flex-col gap-1">
              <h3 className="text-[13px] font-semibold text-muted">{t("debug_why_issues")}</h3>
              <ul className="flex flex-col gap-1 text-xs">
                {h.issues.map((i) => (
                  <li key={i.ref}>
                    <a href={i.url} target="_blank" rel="noreferrer" className="hover:underline">
                      <span className="font-mono text-muted">{i.ref}</span> {i.title}
                    </a>{" "}
                    <span className="text-muted">({i.state})</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function PrTab({ projectId }: { projectId: string }) {
  const { t, language } = useLanguage();
  const [base, setBase] = useState("");
  const [run, setRun] = useState<Run<PreReview>>({ status: "idle" });
  const [copied, setCopied] = useState(false);
  useEffect(() => setRun({ status: "idle" }), [projectId]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setRun({ status: "loading" });
    setCopied(false);
    try {
      setRun({ status: "ok", data: await hub.review(projectId, { base: base.trim() || undefined, language }) });
    } catch (err) {
      setRun({ status: "error", message: errorText(err) });
    }
  };
  const r = run.status === "ok" ? run.data : null;
  const copy = async () => {
    if (!r) return;
    try {
      await navigator.clipboard.writeText(r.description);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <form onSubmit={submit} className="flex flex-col gap-3">
        <p className="text-sm text-ink-2">{t("debug_pr_intro")}</p>
        <label className="flex max-w-xs flex-col gap-1 text-[13px] font-semibold text-muted">
          {t("debug_pr_base")}
          <input className={`${inputClass} font-mono`} value={base} onChange={(e) => setBase(e.target.value)} placeholder="main" />
        </label>
        <button type="submit" className={primaryButton} disabled={run.status === "loading"}>
          {run.status === "loading" ? t("work_loading") : t("debug_pr_run")}
        </button>
      </form>
      {run.status === "loading" && <p className="text-sm text-ink-2">{t("debug_pr_loading")}</p>}
      {run.status === "error" && (
        <p role="alert" className="rounded-xl border border-danger-line bg-danger-bg px-4 py-3 text-sm text-danger-ink">
          {run.message}
        </p>
      )}
      {r && r.files.length === 0 && <p className="text-sm text-ink-2">{t("debug_pr_none", { base: r.base })}</p>}
      {r && r.files.length > 0 && (
        <div className="flex flex-col gap-4">
          <p className="font-mono text-[13px] text-ink-2">
            {t("debug_pr_header", { branch: r.branch || "HEAD", base: r.base, files: r.files.length })}
            {r.issues.length > 0 && <> · {r.issues.join(" ")}</>}
          </p>
          <ul className="flex flex-col gap-0.5 font-mono text-xs">
            {r.files.map((f) => (
              <li key={f.path} className={f.test ? "text-muted" : "text-ink"}>
                <span className="inline-block w-4 font-semibold">{f.status}</span> {f.path} <span className="text-muted">+{f.added} −{f.removed}</span>
              </li>
            ))}
          </ul>
          {r.functions.length > 0 && (
            <div className="flex flex-col gap-1.5">
              <h3 className="text-[13px] font-semibold text-muted">{t("debug_pr_functions")}</h3>
              <ul className="flex flex-col gap-1 text-sm">
                {r.functions.map((fn) => (
                  <li key={`${fn.file_path}-${fn.name}`} className="flex flex-wrap items-baseline gap-x-2">
                    <span className="font-mono font-semibold">{fn.name}</span>
                    <span className="font-mono text-xs text-muted">{fn.file_path}</span>
                    <span className="text-xs text-ink-2">{fn.new ? t("debug_pr_new") : t("debug_pr_callers", { n: fn.callers })}</span>
                    {fn.missing_test && (
                      <span className="rounded border border-danger-line bg-danger-bg px-1.5 text-xs text-danger-ink">{t("debug_pr_no_tests")}</span>
                    )}
                    {fn.tested_in_change && fn.tests.length === 0 && <span className="text-xs text-ink-2">{t("debug_pr_tested_here")}</span>}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {r.tests_to_run.length > 0 && (
            <div className="flex flex-col gap-1">
              <h3 className="text-[13px] font-semibold text-muted">{t("debug_pr_tests")}</h3>
              <p className="break-words font-mono text-xs">{r.tests_to_run.join("  ")}</p>
            </div>
          )}
          {r.verified_answers.length > 0 && (
            <div className="flex flex-col gap-1 rounded-xl border border-line-strong bg-surface-2 px-4 py-3 text-sm">
              <h3 className="font-semibold">{t("debug_pr_answers")}</h3>
              {r.verified_answers.map((a) => (
                <p key={a.id}>
                  «{a.question}» · {a.reviewer} · <span className="font-mono text-xs">{a.files.join(", ")}</span>
                </p>
              ))}
            </div>
          )}
          {r.verified_docs.length > 0 && (
            <div className="flex flex-col gap-1 rounded-xl border border-line-strong bg-surface-2 px-4 py-3 text-sm">
              <h3 className="font-semibold">{t("debug_pr_docs")}</h3>
              <p className="font-mono text-xs">{r.verified_docs.map((d) => d.file_path).join(", ")}</p>
            </div>
          )}
          <Experts experts={r.reviewers} label={t("debug_pr_reviewers")} />
          {r.description && (
            <div className="flex flex-col gap-2">
              <div className="flex items-center justify-between">
                <h3 className="text-[13px] font-semibold text-muted">{t("debug_pr_description")}</h3>
                <button type="button" className={`${secondaryButton} flex items-center gap-1.5`} onClick={copy}>
                  <Copy size={14} /> {copied ? t("standup_copied") : t("standup_copy")}
                </button>
              </div>
              <div className="rounded-xl border border-line bg-surface px-4 py-3">
                <MarkdownViewer content={r.description} />
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function Debug() {
  const { t } = useLanguage();
  const { projectId, projects } = useProject();
  const [params] = useSearchParams();
  const fromUrl = (): Tab => (params.get("tab") === "why" ? "why" : params.get("tab") === "pr" ? "pr" : "error");
  const [tab, setTab] = useState<Tab>(fromUrl);
  const project = projects.find((p) => p.id === projectId);
  useEffect(() => {
    if (params.get("tab")) setTab(fromUrl());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params]);
  const tabs = [
    { id: "error" as const, label: t("debug_tab_error"), icon: Bug },
    { id: "why" as const, label: t("debug_tab_why"), icon: History },
    { id: "pr" as const, label: t("debug_tab_pr"), icon: GitPullRequest },
  ];
  return (
    <div className="flex flex-col gap-6 px-10 pb-10 pt-6">
      <div className="flex flex-col gap-1 rounded-2xl bg-gradient-to-r from-[var(--hero-from)] to-[var(--hero-to)] px-6 py-5 text-white shadow-sm">
        <h1 className="font-display text-[24px] font-semibold">{t("debug_title")}</h1>
        <p className="text-[13px] text-white/85">{project ? t("debug_subtitle", { project: project.name }) : t("no_projects")}</p>
      </div>
      {projectId && (
        <>
          <div role="tablist" className="flex flex-wrap gap-1.5">
            {tabs.map(({ id, label, icon: Icon }) => (
              <button
                key={id}
                type="button"
                role="tab"
                aria-selected={tab === id}
                onClick={() => setTab(id)}
                className={`flex h-[34px] items-center gap-1.5 rounded-lg px-3 text-sm ${tab === id ? "bg-accent text-accent-on" : "bg-surface-2 text-ink-2 hover:text-ink"}`}
              >
                <Icon size={15} /> {label}
              </button>
            ))}
          </div>
          <section className={`${cardClass} max-w-4xl`}>
            {tab === "error" && <ErrorTab projectId={projectId} />}
            {tab === "why" && <WhyTab key={params.toString() + projectId} projectId={projectId} />}
            {tab === "pr" && <PrTab projectId={projectId} />}
          </section>
        </>
      )}
    </div>
  );
}
