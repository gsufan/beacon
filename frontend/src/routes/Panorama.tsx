import { BookCheck, CalendarRange, Gauge, Network, Radar, Send, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { useAuth } from "../components/AuthProvider";
import { Avatar } from "../components/Colorful";
import { ImpactView } from "../components/IssueTools";
import { useLanguage } from "../components/LanguageSelector";
import { useProject } from "../components/ProjectSelector";
import { ApiError } from "../lib/api";
import { hub, type Impact, type Insights, type KnowledgeMap, type SprintSummary, type VerifiedAnswerItem } from "../lib/hub";
import { cardClass, inputClass, primaryButton, secondaryButton } from "../lib/ui";

type Tab = "knowledge" | "sprint" | "gaps" | "impact" | "verified";
type Load<T> = { status: "loading" } | { status: "ok"; data: T } | { status: "error"; message: string };

const errorText = (e: unknown) => (e instanceof ApiError ? e.message : String(e));

function useLoad<T>(fn: (() => Promise<T>) | null, deps: unknown[]): [Load<T>, () => void] {
  const [state, setState] = useState<Load<T>>({ status: "loading" });
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!fn) return;
    let alive = true;
    setState({ status: "loading" });
    fn().then(
      (data) => alive && setState({ status: "ok", data }),
      (e) => alive && setState({ status: "error", message: errorText(e) }),
    );
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);
  return [state, () => setTick((n) => n + 1)];
}

function Status<T>({ load, children }: { load: Load<T>; children: (data: T) => React.ReactNode }) {
  const { t } = useLanguage();
  if (load.status === "loading") return <p className="text-sm text-ink-2">{t("work_loading")}</p>;
  if (load.status === "error") return <p className="rounded-lg bg-danger-bg px-3 py-2 text-sm text-danger-ink">{load.message}</p>;
  return <>{children(load.data)}</>;
}

const riskClass: Record<string, string> = {
  alto: "bg-danger-bg text-danger-ink",
  medio: "bg-accent-tint text-accent-ink",
  bajo: "bg-surface-2 text-ok",
};

function KnowledgeTab({ projectId }: { projectId: string }) {
  const { t } = useLanguage();
  const [load] = useLoad<KnowledgeMap>(() => hub.knowledge(projectId), [projectId]);
  return (
    <Status load={load}>
      {(km) => (
        <div className="flex flex-col gap-3">
          <p className="text-sm text-ink-2">{t("pano_knowledge_intro", { bus: km.project_bus_factor, people: km.people })}</p>
          <div className="overflow-x-auto rounded-xl border border-line">
            <table className="w-full text-left text-[13px]">
              <thead className="bg-surface-2 text-xs text-muted">
                <tr>
                  <th className="px-3 py-2">{t("pano_module")}</th>
                  <th className="px-3 py-2">{t("pano_bus_factor")}</th>
                  <th className="px-3 py-2">{t("pano_experts")}</th>
                  <th className="px-3 py-2">{t("pano_files")}</th>
                  <th className="px-3 py-2">{t("pano_tests")}</th>
                </tr>
              </thead>
              <tbody>
                {km.modules.map((m) => (
                  <tr key={m.module} className="border-t border-line">
                    <td className="px-3 py-2 font-mono">{m.module}</td>
                    <td className="px-3 py-2">
                      <span className={`whitespace-nowrap rounded-full px-2 py-px text-[11px] font-semibold ${riskClass[m.risk] ?? ""}`}>
                        {m.bus_factor} · {t(`pano_risk_${m.risk}` as "pano_risk_alto")}
                      </span>
                    </td>
                    <td className="px-3 py-2">
                      <span className="flex flex-wrap gap-2">
                        {m.experts.slice(0, 3).map((e) => (
                          <span key={e.name} className="inline-flex items-center gap-1">
                            <Avatar name={e.name} size={18} /> {e.name} <span className="text-muted">({e.commits})</span>
                          </span>
                        ))}
                      </span>
                    </td>
                    <td className="px-3 py-2">{m.files}</td>
                    <td className="px-3 py-2">{m.tests}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-xs text-muted">{t("pano_knowledge_hint")}</p>
        </div>
      )}
    </Status>
  );
}

function summaryText(s: SprintSummary, t: ReturnType<typeof useLanguage>["t"]) {
  const lines = [t("pano_sprint_slack_head", { days: s.days, commits: s.commits })];
  for (const g of s.issues.slice(0, 12)) lines.push(`• ${g.issue}: ${g.commits.length} — ${g.commits[0]?.message ?? ""}`);
  return lines.join("\n");
}

function SprintTab({ projectId }: { projectId: string }) {
  const { t } = useLanguage();
  const [days, setDays] = useState(14);
  const [load] = useLoad<SprintSummary>(() => hub.summary(projectId, days), [projectId, days]);
  const [shared, setShared] = useState<string | null>(null);
  return (
    <div className="flex flex-col gap-3">
      <label className="flex items-center gap-2 text-sm">
        {t("pano_sprint_days")}
        <select className="h-[30px] rounded-lg border border-line-strong bg-surface px-2 text-[13px]" value={days} onChange={(e) => setDays(Number(e.target.value))}>
          {[7, 14, 30, 60, 90].map((d) => (
            <option key={d} value={d}>
              {d}
            </option>
          ))}
        </select>
      </label>
      <Status load={load}>
        {(s) => (
          <div className="flex flex-col gap-3">
            <p className="text-sm text-ink-2">{t("pano_sprint_intro", { commits: s.commits, people: s.authors.length, days: s.days })}</p>
            <div className="flex flex-wrap gap-2 text-xs">
              {s.modules.slice(0, 8).map((m) => (
                <span key={m.module} className="rounded-full bg-surface-2 px-2 py-1 font-mono">
                  {m.module} · {m.changes}
                </span>
              ))}
            </div>
            <ul className="flex flex-col gap-2">
              {s.issues.map((g) => (
                <li key={g.issue} className="rounded-lg border border-line px-3 py-2 text-[13px]">
                  <span className="font-semibold">{g.issue}</span>
                  <ul className="mt-1 flex flex-col gap-0.5 text-xs text-ink-2">
                    {g.commits.slice(0, 4).map((c) => (
                      <li key={c.sha}>
                        <span className="font-mono text-muted">{c.sha}</span> {c.message} · {c.author}
                      </li>
                    ))}
                  </ul>
                </li>
              ))}
            </ul>
            <div className="flex items-center gap-2">
              <button
                type="button"
                className={`${secondaryButton} flex w-fit items-center gap-1.5`}
                onClick={() =>
                  hub.shareSlack(projectId, summaryText(s, t)).then(
                    () => setShared(t("work_slack_done")),
                    (e) => setShared(errorText(e)),
                  )
                }
              >
                <Send size={14} /> {t("pano_sprint_share")}
              </button>
              {shared && <span className="text-xs text-ink-2">{shared}</span>}
            </div>
          </div>
        )}
      </Status>
    </div>
  );
}

function Rate({ value }: { value: number }) {
  return (
    <span className="inline-flex items-center gap-2">
      <span className="h-1.5 w-20 overflow-hidden rounded-full bg-surface-2">
        <span className="block h-full bg-accent" style={{ width: `${Math.round(value * 100)}%` }} />
      </span>
      {Math.round(value * 100)}%
    </span>
  );
}

function GapsTab({ projectId }: { projectId: string }) {
  const { t } = useLanguage();
  const [load] = useLoad<Insights>(() => hub.insights(projectId), [projectId]);
  return (
    <Status load={load}>
      {(d) => (
        <div className="flex flex-col gap-5">
          <p className="text-sm text-ink-2">{t("pano_gaps_intro", { questions: d.questions, no_info: d.no_info })}</p>
          {d.questions === 0 ? (
            <p className="text-sm text-muted">{t("pano_gaps_empty")}</p>
          ) : (
            <>
              <div className="flex flex-col gap-2">
                <h3 className="text-sm font-semibold">{t("pano_radar_title")}</h3>
                <p className="text-xs text-muted">{t("pano_radar_hint")}</p>
                {d.radar.map((r) => (
                  <div key={r.module} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-line px-3 py-2 text-[13px]">
                    <span className="font-mono">{r.module}</span>
                    <span className="flex items-center gap-3 text-xs text-ink-2">
                      {t("pano_questions", { n: r.questions })} <Rate value={r.no_info_rate} />
                    </span>
                  </div>
                ))}
              </div>
              <div className="flex flex-col gap-2">
                <h3 className="text-sm font-semibold">{t("pano_gaps_title")}</h3>
                <p className="text-xs text-muted">{t("pano_gaps_hint")}</p>
                {d.gaps.map((r) => (
                  <div key={r.module} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-line px-3 py-2 text-[13px]">
                    <span className="font-mono">{r.module}</span>
                    <span className="flex items-center gap-3 text-xs text-ink-2">
                      {t("pano_questions", { n: r.questions })} · {t("pano_verified_docs", { v: r.verified, n: r.docs })} <Rate value={r.verified_rate} />
                    </span>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      )}
    </Status>
  );
}

function ImpactTab({ projectId }: { projectId: string }) {
  const { t } = useLanguage();
  const [names, setNames] = useState("");
  const [state, setState] = useState<Load<Impact> | null>(null);
  const run = async () => {
    const targets = names.split(/[\s,]+/).filter(Boolean).slice(0, 10);
    if (targets.length === 0) return;
    setState({ status: "loading" });
    try {
      setState({ status: "ok", data: await hub.impact(projectId, targets) });
    } catch (e) {
      setState({ status: "error", message: errorText(e) });
    }
  };
  return (
    <div className="flex flex-col gap-3">
      <p className="text-sm text-ink-2">{t("pano_impact_intro")}</p>
      <form
        className="flex flex-wrap gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          run();
        }}
      >
        <input className={`${inputClass} min-w-[260px] flex-1 font-mono`} value={names} onChange={(e) => setNames(e.target.value)} placeholder="parse_dict_header, _encode_files" aria-label={t("pano_impact_label")} />
        <button type="submit" className={primaryButton} disabled={!names.trim() || state?.status === "loading"}>
          {t("pano_impact_run")}
        </button>
      </form>
      {state && <Status load={state}>{(impact) => <ImpactView impact={impact} />}</Status>}
    </div>
  );
}

function VerifiedTab({ projectId, canVerify }: { projectId: string; canVerify: boolean }) {
  const { t, language } = useLanguage();
  const [load, reload] = useLoad<{ answers: VerifiedAnswerItem[] }>(() => hub.verifiedAnswers(projectId), [projectId]);
  return (
    <Status load={load}>
      {({ answers }) =>
        answers.length === 0 ? (
          <p className="text-sm text-muted">{t("pano_verified_empty")}</p>
        ) : (
          <ul className="flex flex-col gap-2">
            {answers.map((a) => (
              <li key={a.id} className="flex flex-col gap-1 rounded-lg border border-line px-3 py-2 text-[13px]">
                <div className="flex items-start justify-between gap-2">
                  <span className="font-semibold">{a.question}</span>
                  {canVerify && (
                    <button
                      type="button"
                      className="shrink-0 text-muted hover:text-danger-ink"
                      aria-label={t("pano_verified_delete")}
                      title={t("pano_verified_delete")}
                      onClick={() => hub.deleteVerifiedAnswer(projectId, a.id).then(reload, () => {})}
                    >
                      <Trash2 size={14} />
                    </button>
                  )}
                </div>
                <p className="line-clamp-3 text-xs text-ink-2">{a.answer}</p>
                <p className="text-xs text-muted">
                  {t("pano_verified_by", { name: a.reviewer, date: new Date(a.ts * 1000).toLocaleDateString(language) })}
                  {a.corrected && ` · ${t("pano_verified_corrected")}`}
                  {!a.valid && ` · ${t("pano_verified_invalid")}`}
                  {a.stale_files.length > 0 && ` · ${t("pano_verified_stale", { files: a.stale_files.join(", ") })}`}
                </p>
              </li>
            ))}
          </ul>
        )
      }
    </Status>
  );
}

// Team view for the people who guide the project: who knows what, what changed, where the code is hard and what is verified.
export default function Panorama() {
  const { t } = useLanguage();
  const { projectId, projects } = useProject();
  const { isAdmin, roleIn } = useAuth();
  const [tab, setTab] = useState<Tab>("knowledge");
  const project = projects.find((p) => p.id === projectId);
  const role = roleIn(projectId);
  const canVerify = isAdmin || role === "tech_lead";
  const tabs: { id: Tab; label: string; icon: typeof Gauge }[] = [
    { id: "knowledge", label: t("pano_tab_knowledge"), icon: Network },
    { id: "sprint", label: t("pano_tab_sprint"), icon: CalendarRange },
    { id: "gaps", label: t("pano_tab_gaps"), icon: Radar },
    { id: "impact", label: t("pano_tab_impact"), icon: Gauge },
    { id: "verified", label: t("pano_tab_verified"), icon: BookCheck },
  ];

  return (
    <div className="flex flex-col gap-6 px-10 pb-10 pt-6">
      <div className="flex flex-col gap-1 rounded-2xl bg-gradient-to-r from-[var(--hero-from)] to-[var(--hero-to)] px-6 py-5 text-white shadow-sm">
        <h1 className="font-display text-[24px] font-semibold">{t("pano_title")}</h1>
        <p className="text-[13px] text-white/85">{project ? t("pano_subtitle", { project: project.name }) : t("no_projects")}</p>
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
          <section className={cardClass}>
            {tab === "knowledge" && <KnowledgeTab projectId={projectId} />}
            {tab === "sprint" && <SprintTab projectId={projectId} />}
            {tab === "gaps" && <GapsTab projectId={projectId} />}
            {tab === "impact" && <ImpactTab projectId={projectId} />}
            {tab === "verified" && <VerifiedTab projectId={projectId} canVerify={canVerify} />}
          </section>
        </>
      )}
    </div>
  );
}
