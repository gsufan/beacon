import { ArrowRight, TriangleAlert } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import MarkdownViewer from "../components/MarkdownViewer";
import SourceCitation from "../components/SourceCitation";
import { useLanguage } from "../components/LanguageSelector";
import { useProject } from "../components/ProjectSelector";
import { api, ApiError } from "../lib/api";
import type { AIProviderConfig, AIStatus, QueryResponse } from "../lib/types";

interface Turn {
  id: number;
  question: string;
  status: "loading" | "done" | "error";
  result?: QueryResponse;
  error?: string;
}

function NumberBadge({ n, active }: { n: number; active: boolean }) {
  return (
    <span
      className={`flex h-[22px] w-[22px] shrink-0 items-center justify-center rounded-full text-xs font-semibold ${
        active ? "bg-accent text-accent-on" : "bg-surface-2 text-ink-2"
      }`}
    >
      {n}
    </span>
  );
}

function SkeletonLines({ widths }: { widths: string[] }) {
  return (
    <div className="flex flex-col gap-2.5" aria-hidden="true">
      {widths.map((w, i) => (
        <div key={i} className="h-3 animate-pulse rounded-md bg-accent-tint" style={{ width: w }} />
      ))}
    </div>
  );
}

function SourcesPanel({ turn, index }: { turn: Turn | null; index: number }) {
  const { t } = useLanguage();

  if (!turn) {
    return (
      <aside className="flex w-full shrink-0 flex-col gap-3 lg:w-[340px]">
        <div className="flex flex-col gap-1.5 border-b border-line pb-3">
          <h2 className="text-[15px] font-semibold">{t("chat_sources")}</h2>
          <p className="text-[13px] text-ink-2">{t("sources_intro")}</p>
        </div>
        <div className="flex flex-col gap-1 rounded-xl border border-line bg-surface px-3.5 py-3">
          <div className="text-[13px] font-semibold text-accent-ink">{t("sources_semantic")}</div>
          <p className="text-[13px] text-ink-2">{t("sources_semantic_hint")}</p>
        </div>
        <div className="flex flex-col gap-1 rounded-xl border border-dashed border-graph-line bg-graph-bg px-3.5 py-3">
          <div className="text-[13px] font-semibold text-graph-ink">{t("sources_graph")}</div>
          <p className="text-[13px] text-ink-2">{t("sources_graph_hint")}</p>
        </div>
      </aside>
    );
  }

  const sources = turn.result?.sources ?? [];
  const semantic = sources.filter((s) => !s.expanded);
  const graph = sources.filter((s) => s.expanded);
  const summary =
    turn.status === "loading"
      ? t("sources_searching")
      : sources.length > 0
        ? t("sources_count", { n: sources.length })
        : t("sources_none");

  return (
    <aside
      aria-busy={turn.status === "loading"}
      className="flex w-full shrink-0 flex-col gap-3 lg:w-[340px] lg:overflow-y-auto"
    >
      <div className="flex flex-col gap-1.5 border-b border-line pb-3">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <NumberBadge n={index} active />
            <h2 className="text-[15px] font-semibold">{t("sources_of", { n: index })}</h2>
          </div>
          <span className="text-xs text-muted">{summary}</span>
        </div>
        <p className="truncate text-[13px] text-ink-2" title={turn.question}>
          {turn.question}
        </p>
      </div>

      {turn.status === "loading" &&
        [0, 1, 2].map((i) => (
          <div key={i} className="rounded-xl border border-line bg-surface p-3.5">
            <SkeletonLines widths={["55%", "80%"]} />
          </div>
        ))}

      {turn.status === "error" && (
        <div className="flex flex-col gap-1 rounded-xl border border-dashed border-line-strong px-3.5 py-3">
          <div className="text-[13px] font-semibold">{t("sources_failed_title")}</div>
          <p className="text-[13px] text-ink-2">{t("sources_failed_body")}</p>
        </div>
      )}

      {semantic.length > 0 && (
        <>
          <div className="text-xs font-semibold text-muted">
            {t("sources_semantic")} · {semantic.length}
          </div>
          {semantic.map((s, i) => (
            <SourceCitation key={`${s.file_path}-${s.start_line}-${i}`} source={s} defaultOpen={i === 0} />
          ))}
        </>
      )}

      {graph.length > 0 && (
        <>
          <div className="flex flex-col gap-0.5 pt-1.5">
            <div className="text-xs font-semibold text-graph-ink">
              {t("sources_graph")} · {graph.length}
            </div>
            <p className="text-xs text-muted">{t("sources_graph_hint")}</p>
          </div>
          {graph.map((s, i) => (
            <SourceCitation key={`${s.file_path}-${s.start_line}-${i}`} source={s} />
          ))}
        </>
      )}
    </aside>
  );
}

export default function Chat() {
  const { projects, projectId } = useProject();
  const { language, t } = useLanguage();
  const [question, setQuestion] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [copiedId, setCopiedId] = useState<number | null>(null);
  const [aiProvider, setAiProvider] = useState<AIProviderConfig | null>(null);
  const [aiStatus, setAiStatus] = useState<AIStatus | null>(null);
  const nextId = useRef(1);
  const endRef = useRef<HTMLDivElement>(null);

  const projectName = projects.find((p) => p.id === projectId)?.name ?? projectId ?? "";
  const loading = turns.some((turn) => turn.status === "loading");
  const selectedIndex = turns.findIndex((turn) => turn.id === selectedId);
  const selected = selectedIndex >= 0 ? turns[selectedIndex] : null;

  // Each project has its own conversation.
  useEffect(() => {
    setTurns([]);
    setSelectedId(null);
  }, [projectId]);

  const checkAiStatus = () => {
    api
      .aiStatus()
      .then(setAiStatus)
      .catch(() => setAiStatus(null));
  };

  useEffect(() => {
    api
      .getConfig()
      .then((cfg) => setAiProvider(cfg.ai_provider))
      .catch(() => {});
    checkAiStatus();
  }, []);

  useEffect(() => {
    endRef.current?.scrollIntoView?.({ block: "end" });
  }, [turns.length]);

  const patchTurn = (id: number, patch: Partial<Turn>) =>
    setTurns((prev) => prev.map((turn) => (turn.id === id ? { ...turn, ...patch } : turn)));

  const run = async (id: number, text: string) => {
    if (!projectId) return;
    patchTurn(id, { status: "loading", error: undefined, result: undefined });
    try {
      const result = await api.query(projectId, text, 5, language);
      patchTurn(id, { status: "done", result });
    } catch (e) {
      patchTurn(id, { status: "error", error: e instanceof ApiError ? e.message : t("settings_error_generic") });
      checkAiStatus();
    }
  };

  const ask = (text: string) => {
    const trimmed = text.trim();
    if (!projectId || loading || trimmed.length < 3) return;
    const id = nextId.current++;
    setTurns((prev) => [...prev, { id, question: trimmed, status: "loading" }]);
    setSelectedId(id);
    setQuestion("");
    run(id, trimmed);
  };

  const copyAnswer = async (turn: Turn) => {
    try {
      await navigator.clipboard.writeText(turn.result?.answer ?? "");
      setCopiedId(turn.id);
      setTimeout(() => setCopiedId(null), 2000);
    } catch {
      // Clipboard may be blocked on plain-HTTP origins; nothing to do.
    }
  };

  const host = aiProvider?.ollama_host.replace(/^https?:\/\//, "");
  const aiProblem = !aiStatus
    ? null
    : !aiStatus.reachable
      ? t("ai_unreachable")
      : !aiStatus.llm_model_available || !aiStatus.embedding_model_available
        ? t("ai_model_missing")
        : null;

  return (
    <div className="flex h-full flex-col gap-6 px-10 pb-7 pt-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="font-display text-[22px] font-semibold">{t("chat_title")}</h1>
        {aiProvider && (
          <div
            className={`flex min-h-[30px] flex-wrap items-center gap-2 rounded-full border bg-surface px-3 text-[13px] text-ink-2 ${
              aiProblem ? "border-danger-line" : "border-line"
            }`}
          >
            {aiProblem && (
              <span role="status" className="flex items-center gap-1.5 font-semibold text-danger-ink">
                <span className="h-2 w-2 rounded-sm bg-danger-ink" />
                {aiProblem}
              </span>
            )}
            {aiStatus && !aiProblem && <span className="h-2 w-2 rounded-full bg-ok" />}
            <span>
              {aiProvider.provider} · {host}
            </span>
            <span className="font-mono text-xs text-ink">{aiProvider.llm_model}</span>
          </div>
        )}
      </header>

      <div className="flex min-h-0 flex-1 flex-col gap-8 lg:flex-row">
        <section className="flex min-h-0 min-w-0 flex-1 flex-col gap-4">
          <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto">
            {turns.length === 0 && projectId && (
              <div className="flex flex-1 flex-col justify-center gap-5 py-6">
                <div className="flex flex-col gap-1.5">
                  <h2 className="font-display text-[28px] font-semibold leading-tight">
                    {t("chat_empty_title", { project: projectName })}
                  </h2>
                  <p className="max-w-[520px] text-[15px] text-ink-2">{t("chat_empty_body")}</p>
                </div>
                <div className="flex max-w-[560px] flex-col gap-2">
                  <div className="text-xs font-semibold text-muted">{t("chat_examples")}</div>
                  {[t("chat_example_1"), t("chat_example_2"), t("chat_example_3")].map((example) => (
                    <button
                      key={example}
                      type="button"
                      onClick={() => ask(example)}
                      className="flex min-h-[44px] items-center justify-between gap-3 rounded-[10px] border border-line bg-surface px-3.5 text-left text-[15px] hover:border-line-strong"
                    >
                      <span>{example}</span>
                      <ArrowRight size={16} className="shrink-0 text-accent-ink" aria-hidden="true" />
                    </button>
                  ))}
                </div>
              </div>
            )}

            {turns.length === 0 && !projectId && (
              <div className="flex flex-1 flex-col justify-center gap-2 py-6">
                <h2 className="font-display text-[28px] font-semibold leading-tight">{t("chat_no_project_title")}</h2>
                <p className="max-w-[520px] text-[15px] text-ink-2">{t("chat_no_project_body")}</p>
                <Link to="/settings" className="w-fit text-sm font-semibold text-accent-ink hover:underline">
                  {t("chat_no_project_link")}
                </Link>
              </div>
            )}

            {turns.map((turn, i) =>
              turn.id !== selectedId ? (
                <button
                  key={turn.id}
                  type="button"
                  onClick={() => setSelectedId(turn.id)}
                  className="flex w-full shrink-0 items-center gap-3 rounded-[10px] border border-line bg-surface px-3.5 py-2.5 text-left text-ink-2 hover:border-line-strong"
                >
                  <NumberBadge n={i + 1} active={false} />
                  <span className="min-w-0 flex-1 truncate">{turn.question}</span>
                  <span className="shrink-0 text-xs text-muted">
                    {turn.status === "done"
                      ? t("chat_view_sources", { n: turn.result?.sources.length ?? 0 })
                      : turn.status === "loading"
                        ? t("chat_asking")
                        : t("chat_turn_failed")}
                  </span>
                </button>
              ) : (
                <div key={turn.id} className="flex shrink-0 flex-col gap-4">
                  <div className="flex flex-col gap-1.5">
                    <div className="flex items-center gap-2">
                      <NumberBadge n={i + 1} active />
                      <span className="text-xs font-semibold text-accent-ink">{t("chat_selected")}</span>
                    </div>
                    <div className="text-lg font-semibold leading-snug">{turn.question}</div>
                  </div>

                  {turn.status === "loading" && (
                    <article aria-busy="true" className="flex flex-col gap-3.5 rounded-[14px] border border-line bg-surface px-5 py-5">
                      <div role="status" className="flex items-center gap-2.5">
                        <span className="h-2.5 w-2.5 animate-pulse rounded-full bg-accent" />
                        <span className="text-[15px] font-semibold">{t("chat_asking")}</span>
                      </div>
                      <p className="text-sm text-ink-2">{t("chat_loading_body")}</p>
                      <SkeletonLines widths={["92%", "78%", "85%", "40%"]} />
                    </article>
                  )}

                  {turn.status === "done" && turn.result && (
                    <article className="flex flex-col gap-3 rounded-[14px] border border-line bg-surface px-5 py-5">
                      <MarkdownViewer content={turn.result.answer} />
                      <div className="flex flex-wrap items-center gap-2 pt-1">
                        <button
                          type="button"
                          onClick={() => copyAnswer(turn)}
                          className="h-[30px] rounded-lg border border-line-strong bg-surface px-3 text-[13px] hover:bg-surface-2"
                        >
                          {copiedId === turn.id ? t("chat_copied") : t("chat_copy")}
                        </button>
                        <span className="text-xs text-muted">
                          {t("chat_based_on", { n: turn.result.sources.length })}
                        </span>
                      </div>
                    </article>
                  )}

                  {turn.status === "error" && (
                    <article role="alert" className="flex flex-col gap-3 rounded-[14px] border border-danger-line bg-danger-bg px-5 py-5">
                      <div className="flex items-center gap-2.5 text-danger-ink">
                        <TriangleAlert size={20} aria-hidden="true" />
                        <span className="text-base font-semibold">{t("chat_error_title")}</span>
                      </div>
                      <p className="text-sm">{t("chat_error_body")}</p>
                      <div className="flex flex-col gap-1">
                        <div className="text-xs font-semibold text-muted">{t("chat_error_detail")}</div>
                        <pre className="whitespace-pre-wrap rounded-lg border border-danger-line bg-surface px-3 py-2.5 font-mono text-xs leading-relaxed">
                          {turn.error}
                        </pre>
                      </div>
                      <div className="flex flex-wrap items-center gap-3">
                        <button
                          type="button"
                          onClick={() => run(turn.id, turn.question)}
                          className="h-9 rounded-[9px] bg-accent px-4 font-semibold text-accent-on hover:opacity-90"
                        >
                          {t("chat_retry")}
                        </button>
                        <Link to="/settings" className="text-sm font-semibold text-accent-ink hover:underline">
                          {t("chat_error_link")}
                        </Link>
                      </div>
                    </article>
                  )}
                </div>
              )
            )}
            <div ref={endRef} />
          </div>

          <form
            onSubmit={(e) => {
              e.preventDefault();
              ask(question);
            }}
            className="flex shrink-0 flex-col gap-2.5 rounded-[14px] border border-line-strong bg-surface px-3.5 py-3 focus-within:border-accent"
          >
            <label htmlFor="chat-question" className="text-xs font-semibold text-muted">
              {t(turns.length === 0 ? "chat_label_first" : "chat_label_next", { project: projectName })}
            </label>
            <textarea
              id="chat-question"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) ask(question);
              }}
              rows={2}
              disabled={loading || !projectId}
              placeholder={t("chat_placeholder")}
              className="w-full resize-none bg-transparent text-[15px] outline-none placeholder:text-muted disabled:opacity-60"
            />
            <div className="flex items-center justify-between gap-3">
              <span className="text-xs text-muted">{t("chat_send_hint")}</span>
              <button
                type="submit"
                disabled={loading || !projectId}
                className="h-9 rounded-[9px] bg-accent px-4 font-semibold text-accent-on transition-opacity hover:opacity-90 disabled:opacity-40"
              >
                {loading ? t("chat_asking") : t("chat_ask")}
              </button>
            </div>
          </form>
        </section>

        <SourcesPanel turn={selected} index={selectedIndex + 1} />
      </div>
    </div>
  );
}
