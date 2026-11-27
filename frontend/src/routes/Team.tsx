import { Copy, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useAuth } from "../components/AuthProvider";
import { useLanguage } from "../components/LanguageSelector";
import { useProject } from "../components/ProjectSelector";
import { ApiError } from "../lib/api";
import { hub, ROLES, type AuditEvent, type HubProject, type Installation, type Invitation, type Org, type Plan, type Role, type TeamUser } from "../lib/hub";
import { cardClass, inputClass, primaryButton, secondaryButton } from "../lib/ui";
import { Avatar, RoleBadge } from "../components/Colorful";

function Section({ title, description, children }: { title: string; description?: string; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-3">
      <div className="flex flex-col gap-0.5">
        <h2 className="text-[17px] font-semibold">{title}</h2>
        {description && <p className="text-[13px] text-ink-2">{description}</p>}
      </div>
      {children}
    </section>
  );
}

const selectClass = "h-[30px] rounded-lg border border-line-strong bg-surface px-2 text-[13px]";

export default function Team() {
  const { t, language } = useLanguage();
  const { user, isAdmin, signIn, refresh } = useAuth();
  const { projects: localProjects } = useProject();
  const [org, setOrg] = useState<Org | null>(null);
  const [plans, setPlans] = useState<Plan[]>([]);
  const [users, setUsers] = useState<TeamUser[]>([]);
  const [invitations, setInvitations] = useState<Invitation[]>([]);
  const [projects, setProjects] = useState<HubProject[]>([]);
  const [audit, setAudit] = useState<AuditEvent[] | null>(null);
  const [installations, setInstallations] = useState<Installation[]>([]);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const [inviteEmail, setInviteEmail] = useState("");
  const [lastCode, setLastCode] = useState<{ code: string; email: string } | null>(null);
  const [newProject, setNewProject] = useState({ id: "", name: "", github_repo: "", jira_project: "" });
  const [githubToken, setGithubToken] = useState("");
  const [jira, setJira] = useState({ site: "", email: "", token: "" });
  const [slackWebhook, setSlackWebhook] = useState("");
  const [repoDrafts, setRepoDrafts] = useState<Record<string, string>>({});
  const [jiraDrafts, setJiraDrafts] = useState<Record<string, string>>({});
  const [addMember, setAddMember] = useState<Record<string, { user: string; role: Role }>>({});

  const load = useCallback(() => {
    hub.org().then(setOrg).catch(() => {});
    hub.plans().then((p) => setPlans(p.plans)).catch(() => {});
    hub.users().then((u) => setUsers(u.users)).catch(() => {});
    hub.projects().then((p) => setProjects(p.projects)).catch(() => {});
    hub.audit().then((a) => setAudit(a.events)).catch(() => setAudit(null));
    if (isAdmin) hub.invitations().then((i) => setInvitations(i.invitations)).catch(() => {});
    if (isAdmin) hub.instances().then((i) => setInstallations(i.instances)).catch(() => {});
  }, [isAdmin]);

  useEffect(load, [load]);

  const run = async (fn: () => Promise<unknown>, ok?: string) => {
    setMessage(null);
    try {
      await fn();
      if (ok) setMessage({ ok: true, text: ok });
      load();
      await refresh();
    } catch (e) {
      setMessage({ ok: false, text: e instanceof ApiError ? e.message : String(e) });
    }
  };

  const money = (n: number) => new Intl.NumberFormat(language === "es" ? "es-CL" : "en-US", { style: "currency", currency: "CLP", maximumFractionDigits: 0 }).format(n);
  const dateTime = (ts: number) => new Date(ts * 1000).toLocaleString(language, { dateStyle: "short", timeStyle: "short" });
  const roleLabel = (r: string) => t(`role_${r}` as "role_admin");
  const canManage = (p: HubProject) => isAdmin || p.role === "product_owner";
  const missingLocal = localProjects.filter((lp) => !projects.some((p) => p.id === lp.id));

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-8 px-10 pb-10 pt-6">
      <div className="flex flex-col gap-1">
        <h1 className="font-display text-[22px] font-semibold">{t("nav_team")}</h1>
        <p className="text-[13px] text-ink-2">{org ? org.name : ""}</p>
      </div>

      {message && (
        <p
          role={message.ok ? "status" : "alert"}
          className={`rounded-xl border px-4 py-3 text-sm ${message.ok ? "border-line bg-surface" : "border-danger-line bg-danger-bg text-danger-ink"}`}
        >
          {message.text}
        </p>
      )}

      {org && (
        <Section title={t("team_plan")} description={t("team_plan_hint")}>
          <div className="flex flex-wrap items-center gap-x-8 gap-y-2 rounded-2xl bg-gradient-to-r from-[var(--hero-from)] to-[var(--hero-to)] px-5 py-4 text-sm text-white [&_.text-muted]:text-white/75">
            <div>
              <div className="text-xs text-muted">{t("team_current_plan")}</div>
              <div className="font-semibold">{org.plan_label}</div>
            </div>
            <div>
              <div className="text-xs text-muted">{org.expired ? t("team_expired_on") : t("team_valid_until")}</div>
              <div className={`font-semibold ${org.expired ? "text-danger-ink" : ""}`}>
                {new Date(org.period_end * 1000).toLocaleDateString(language)}
                {!org.expired && <span className="ml-1 font-normal text-white/80">({t("team_days_left", { n: org.days_left })})</span>}
              </div>
            </div>
            <div>
              <div className="text-xs text-muted">{t("team_seats")}</div>
              <div className="font-semibold">
                {org.seats_used} / {org.seats}
              </div>
            </div>
          </div>
          {isAdmin && (
            <div className="grid gap-3 sm:grid-cols-2">
              {plans.map((p, i) => (
                <div key={p.id} className={`${cardClass} flex flex-col gap-2 border-t-4`} style={{ borderTopColor: `var(--c${i === 0 ? 1 : 5})` }}>
                  <div className="flex items-baseline justify-between">
                    <span className="font-semibold">{p.label}</span>
                    <span className="text-sm">
                      {money(p.price_per_user)} <span className="text-xs text-muted">{t("team_per_user_month")}</span>
                    </span>
                  </div>
                  <span className="text-[13px] text-ink-2">{t("team_up_to_users", { n: p.seats })}</span>
                  <button
                    type="button"
                    className={secondaryButton + " w-fit"}
                    onClick={() => run(async () => signIn(await hub.choosePlan(p.id)), t("team_plan_done", { plan: p.label }))}
                  >
                    {org.plan === p.id ? t("team_renew") : t("team_choose")}
                  </button>
                </div>
              ))}
              <p className="text-xs text-muted sm:col-span-2">{t("team_simulated_payment")}</p>
            </div>
          )}
        </Section>
      )}

      <Section title={t("team_people")} description={isAdmin ? t("team_people_hint") : undefined}>
        <div className={`${cardClass} overflow-x-auto p-0`}>
          <table className="w-full text-sm">
            <thead className="text-left text-xs text-muted">
              <tr className="border-b border-line">
                <th className="px-4 py-2 font-semibold">{t("login_name")}</th>
                {isAdmin && <th className="px-4 py-2 font-semibold">{t("login_email")}</th>}
                <th className="px-4 py-2 font-semibold">GitHub</th>
                {org?.jira_connected && <th className="px-4 py-2 font-semibold">Jira</th>}
                {isAdmin && <th className="px-4 py-2" />}
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id} className={`border-b border-line last:border-0 ${u.active ? "" : "text-muted"}`}>
                  <td className="px-4 py-2">
                    <span className="inline-flex items-center gap-2">
                      <Avatar name={u.name} size={24} />
                      {u.name}
                    </span>
                    {u.is_admin ? <span className="ml-2"><RoleBadge role="admin" label={t("role_admin")} /></span> : null}
                    {!u.active && <span className="ml-2 text-xs">({t("team_inactive")})</span>}
                  </td>
                  {isAdmin && <td className="px-4 py-2 text-ink-2">{u.email}</td>}
                  <td className="px-4 py-2">
                    {isAdmin || u.id === user?.sub ? (
                      <input
                        className={`${inputClass} h-[30px] w-40 font-mono text-[13px]`}
                        defaultValue={u.github_login ?? ""}
                        placeholder="login"
                        onBlur={(e) =>
                          e.target.value.trim() !== (u.github_login ?? "") && run(() => hub.patchUser(u.id, { github_login: e.target.value.trim() }))
                        }
                      />
                    ) : (
                      <span className="font-mono text-[13px]">{u.github_login ?? "—"}</span>
                    )}
                  </td>
                  {org?.jira_connected && (
                    <td className="px-4 py-2">
                      {isAdmin || u.id === user?.sub ? (
                        <input
                          className={`${inputClass} h-[30px] w-44 font-mono text-[13px]`}
                          defaultValue={u.jira_user ?? ""}
                          placeholder={t("team_jira_user")}
                          onBlur={(e) =>
                            e.target.value.trim() !== (u.jira_user ?? "") && run(() => hub.patchUser(u.id, { jira_user: e.target.value.trim() }))
                          }
                        />
                      ) : (
                        <span className="font-mono text-[13px]">{u.jira_user ?? "—"}</span>
                      )}
                    </td>
                  )}
                  {isAdmin && (
                    <td className="px-4 py-2 text-right">
                      {u.id !== user?.sub && (
                        <button type="button" className={secondaryButton} onClick={() => run(() => hub.patchUser(u.id, { active: !u.active }))}>
                          {u.active ? t("team_deactivate") : t("team_activate")}
                        </button>
                      )}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {isAdmin && (
          <div className={`${cardClass} flex flex-col gap-3`}>
            <form
              className="flex gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                run(async () => {
                  setLastCode(await hub.invite(inviteEmail));
                  setInviteEmail("");
                });
              }}
            >
              <input className={inputClass} type="email" required placeholder={t("team_invite_email")} value={inviteEmail} onChange={(e) => setInviteEmail(e.target.value)} />
              <button type="submit" className={primaryButton + " shrink-0"}>
                {t("team_invite")}
              </button>
            </form>
            {lastCode && (
              <div className="flex flex-wrap items-center gap-2 rounded-lg bg-accent-tint px-3 py-2 text-sm">
                <span>{t("team_invite_code", { email: lastCode.email })}</span>
                <code className="rounded bg-surface px-2 py-0.5 font-mono">{lastCode.code}</code>
                <button type="button" className="text-accent-ink" aria-label={t("chat_copy")} onClick={() => navigator.clipboard?.writeText(lastCode.code)}>
                  <Copy size={14} />
                </button>
              </div>
            )}
            {invitations.length > 0 && (
              <ul className="flex flex-col gap-1 text-[13px] text-ink-2">
                {invitations.map((i) => (
                  <li key={i.code} className="flex items-center justify-between gap-2">
                    <span>
                      {i.email} · <code className="font-mono">{i.code}</code>
                    </span>
                    <button type="button" className={secondaryButton} onClick={() => run(() => hub.revokeInvitation(i.code))}>
                      {t("team_revoke")}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </Section>

      <Section title={t("team_projects")} description={t("team_projects_hint")}>
        {projects.length === 0 && <p className="text-sm text-muted">{t("team_no_projects")}</p>}
        {projects.map((p) => {
          const outside = users.filter((u) => u.active && !p.members.some((m) => m.id === u.id));
          const draft = addMember[p.id] ?? { user: "", role: "developer" as Role };
          return (
            <div key={p.id} className={`${cardClass} flex flex-col gap-3`}>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <span className="font-semibold">{p.name}</span> <span className="font-mono text-xs text-muted">{p.id}</span>
                </div>
                {isAdmin && (
                  <button
                    type="button"
                    className={`${secondaryButton} flex items-center gap-1`}
                    onClick={() => window.confirm(t("team_delete_project_confirm", { name: p.name })) && run(() => hub.deleteProject(p.id))}
                  >
                    <Trash2 size={13} /> {t("settings_delete")}
                  </button>
                )}
              </div>
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <span className="text-xs font-semibold text-muted">{t("team_github_repo")}</span>
                {canManage(p) ? (
                  <>
                    <input
                      className={`${inputClass} h-[30px] w-56 font-mono text-[13px]`}
                      placeholder="owner/repo"
                      value={repoDrafts[p.id] ?? p.github_repo ?? ""}
                      onChange={(e) => setRepoDrafts({ ...repoDrafts, [p.id]: e.target.value })}
                    />
                    <button type="button" className={secondaryButton} onClick={() => run(() => hub.patchProject(p.id, { github_repo: repoDrafts[p.id] ?? "" }), t("team_saved"))}>
                      {t("settings_save")}
                    </button>
                  </>
                ) : (
                  <span className="font-mono text-[13px]">{p.github_repo ?? "—"}</span>
                )}
              </div>
              {(org?.jira_connected || p.jira_project) && (
                <div className="flex flex-wrap items-center gap-2 text-sm">
                  <span className="text-xs font-semibold text-muted">{t("team_jira_project")}</span>
                  {canManage(p) ? (
                    <>
                      <input
                        className={`${inputClass} h-[30px] w-32 font-mono text-[13px]`}
                        placeholder="CLAVE"
                        value={jiraDrafts[p.id] ?? p.jira_project ?? ""}
                        onChange={(e) => setJiraDrafts({ ...jiraDrafts, [p.id]: e.target.value })}
                      />
                      <button type="button" className={secondaryButton} onClick={() => run(() => hub.patchProject(p.id, { jira_project: jiraDrafts[p.id] ?? "" }), t("team_saved"))}>
                        {t("settings_save")}
                      </button>
                      <span className="text-xs text-muted">{t("team_jira_project_hint")}</span>
                    </>
                  ) : (
                    <span className="font-mono text-[13px]">{p.jira_project ?? "—"}</span>
                  )}
                </div>
              )}
              <ul className="flex flex-col divide-y divide-line text-sm">
                {p.members.map((m) => (
                  <li key={m.id} className="flex items-center justify-between gap-2 py-1.5">
                    <span className="flex items-center gap-2">
                      <Avatar name={m.name} size={24} />
                      {m.name}
                    </span>
                    {canManage(p) ? (
                      <span className="flex items-center gap-2">
                        <select className={selectClass} value={m.role} onChange={(e) => run(() => hub.setMember(p.id, m.id, e.target.value as Role))}>
                          {ROLES.map((r) => (
                            <option key={r} value={r}>
                              {roleLabel(r)}
                            </option>
                          ))}
                        </select>
                        <button type="button" className="text-muted hover:text-danger-ink" aria-label={t("team_remove_member")} onClick={() => run(() => hub.removeMember(p.id, m.id))}>
                          <Trash2 size={14} />
                        </button>
                      </span>
                    ) : (
                      <RoleBadge role={m.role} label={roleLabel(m.role)} />
                    )}
                  </li>
                ))}
                {p.members.length === 0 && <li className="py-1.5 text-muted">{t("team_no_members")}</li>}
              </ul>
              {canManage(p) && outside.length > 0 && (
                <div className="flex flex-wrap items-center gap-2">
                  <select className={selectClass} value={draft.user} onChange={(e) => setAddMember({ ...addMember, [p.id]: { ...draft, user: e.target.value } })}>
                    <option value="">{t("team_add_person")}</option>
                    {outside.map((u) => (
                      <option key={u.id} value={u.id}>
                        {u.name}
                      </option>
                    ))}
                  </select>
                  <select className={selectClass} value={draft.role} onChange={(e) => setAddMember({ ...addMember, [p.id]: { ...draft, role: e.target.value as Role } })}>
                    {ROLES.map((r) => (
                      <option key={r} value={r}>
                        {roleLabel(r)}
                      </option>
                    ))}
                  </select>
                  <button type="button" className={secondaryButton} disabled={!draft.user} onClick={() => run(() => hub.setMember(p.id, Number(draft.user), draft.role))}>
                    {t("team_add")}
                  </button>
                </div>
              )}
            </div>
          );
        })}
        {isAdmin && (
          <form
            className={`${cardClass} flex flex-col gap-2`}
            onSubmit={(e) => {
              e.preventDefault();
              run(async () => {
                await hub.createProject({
                  id: newProject.id,
                  name: newProject.name,
                  github_repo: newProject.github_repo || undefined,
                  jira_project: newProject.jira_project || undefined,
                });
                setNewProject({ id: "", name: "", github_repo: "", jira_project: "" });
              }, t("team_saved"));
            }}
          >
            <span className="text-sm font-semibold">{t("team_add_project")}</span>
            <p className="text-[13px] text-ink-2">{t("team_add_project_hint")}</p>
            <div className={`grid gap-2 ${org?.jira_connected ? "sm:grid-cols-4" : "sm:grid-cols-3"}`}>
              <select
                className={inputClass}
                required
                value={newProject.id}
                onChange={(e) => {
                  const lp = localProjects.find((x) => x.id === e.target.value);
                  setNewProject({ ...newProject, id: e.target.value, name: lp?.name ?? "" });
                }}
              >
                <option value="">{t("team_local_project")}</option>
                {missingLocal.map((lp) => (
                  <option key={lp.id} value={lp.id}>
                    {lp.name}
                  </option>
                ))}
              </select>
              <input className={inputClass} required placeholder={t("settings_name_placeholder")} value={newProject.name} onChange={(e) => setNewProject({ ...newProject, name: e.target.value })} />
              <input className={`${inputClass} font-mono`} placeholder="owner/repo (GitHub)" value={newProject.github_repo} onChange={(e) => setNewProject({ ...newProject, github_repo: e.target.value })} />
              {org?.jira_connected && (
                <input className={`${inputClass} font-mono`} placeholder={t("team_jira_project_placeholder")} value={newProject.jira_project} onChange={(e) => setNewProject({ ...newProject, jira_project: e.target.value })} />
              )}
            </div>
            <button type="submit" className={primaryButton} disabled={!newProject.id}>
              {t("team_add")}
            </button>
          </form>
        )}
      </Section>

      {isAdmin && (
        <Section title={t("team_instances")} description={t("team_instances_hint")}>
          <div className={`${cardClass} overflow-x-auto p-0`}>
            <table className="w-full text-sm">
              <thead className="text-left text-xs text-muted">
                <tr className="border-b border-line">
                  <th className="px-4 py-2 font-semibold">{t("instance_name")}</th>
                  <th className="px-4 py-2 font-semibold">{t("team_instance_kind")}</th>
                  <th className="px-4 py-2 font-semibold">{t("team_instance_last_seen")}</th>
                  <th className="px-4 py-2" />
                </tr>
              </thead>
              <tbody>
                {installations.length === 0 && (
                  <tr>
                    <td colSpan={4} className="px-4 py-3 text-muted">
                      {t("team_instances_none")}
                    </td>
                  </tr>
                )}
                {installations.map((i) => (
                  <tr key={i.id} className={`border-b border-line last:border-0 ${i.revoked ? "text-muted" : ""}`}>
                    <td className="px-4 py-2 font-medium">{i.name}</td>
                    <td className="px-4 py-2 text-ink-2">{i.user_name ? t("team_instance_personal_of", { name: i.user_name }) : t("team_instance_shared")}</td>
                    <td className="px-4 py-2 text-ink-2">{i.last_seen ? dateTime(i.last_seen) : "—"}</td>
                    <td className="px-4 py-2 text-right">
                      {i.revoked ? (
                        <span className="text-xs text-danger-ink">{i.wiped ? t("team_instance_wiped") : t("team_instance_revoked")}</span>
                      ) : (
                        <button
                          type="button"
                          className={secondaryButton}
                          onClick={() => window.confirm(t("team_instance_revoke_confirm", { name: i.name })) && run(() => hub.revokeInstance(i.id), t("team_saved"))}
                        >
                          {t("team_instance_revoke")}
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      )}

      {isAdmin && org && (
        <Section title={t("team_integrations")} description={t("team_integrations_hint")}>
          <form
            className={`${cardClass} flex flex-col gap-2`}
            onSubmit={(e) => {
              e.preventDefault();
              run(async () => {
                await hub.setGitHubToken(githubToken);
                setGithubToken("");
              }, t("team_saved"));
            }}
          >
            <span className="text-sm font-semibold">GitHub</span>
            <span className="text-sm">
              <span className={`mr-2 inline-block h-2 w-2 rounded-full ${org.github_connected ? "bg-ok" : "bg-line-strong"}`} />
              {org.github_connected ? t("team_github_connected") : t("team_github_not_connected")}
            </span>
            <p className="text-xs text-muted">{t("team_github_hint")}</p>
            <div className="flex gap-2">
              <input className={inputClass} type="password" placeholder={t("team_github_token")} value={githubToken} onChange={(e) => setGithubToken(e.target.value)} />
              <button type="submit" className={primaryButton + " shrink-0"}>
                {t("settings_save")}
              </button>
            </div>
          </form>
          <form
            className={`${cardClass} flex flex-col gap-2`}
            onSubmit={(e) => {
              e.preventDefault();
              run(async () => {
                await hub.setJira(jira.site, jira.email, jira.token);
                setJira({ site: "", email: "", token: "" });
              }, t("team_saved"));
            }}
          >
            <span className="text-sm font-semibold">Jira</span>
            <span className="text-sm">
              <span className={`mr-2 inline-block h-2 w-2 rounded-full ${org.jira_connected ? "bg-ok" : "bg-line-strong"}`} />
              {org.jira_connected ? t("team_jira_connected", { site: org.jira_site ?? "" }) : t("team_jira_not_connected")}
            </span>
            <p className="text-xs text-muted">{t("team_jira_hint")}</p>
            <div className="grid gap-2 sm:grid-cols-3">
              <input className={inputClass} placeholder="https://<sitio>.atlassian.net" value={jira.site} onChange={(e) => setJira({ ...jira, site: e.target.value })} />
              <input className={inputClass} type="email" placeholder={t("team_jira_email")} value={jira.email} onChange={(e) => setJira({ ...jira, email: e.target.value })} />
              <input className={inputClass} type="password" placeholder={t("team_jira_token")} value={jira.token} onChange={(e) => setJira({ ...jira, token: e.target.value })} />
            </div>
            <div className="flex gap-2">
              <button type="submit" className={primaryButton}>
                {t("settings_save")}
              </button>
              {org.jira_connected && (
                <button type="button" className={secondaryButton} onClick={() => run(() => hub.setJira("", "", ""), t("team_saved"))}>
                  {t("team_disconnect")}
                </button>
              )}
            </div>
          </form>
          <form
            className={`${cardClass} flex flex-col gap-2`}
            onSubmit={(e) => {
              e.preventDefault();
              run(async () => {
                await hub.setSlack(slackWebhook);
                setSlackWebhook("");
              }, t("team_saved"));
            }}
          >
            <span className="text-sm font-semibold">Slack</span>
            <span className="text-sm">
              <span className={`mr-2 inline-block h-2 w-2 rounded-full ${org.slack_connected ? "bg-ok" : "bg-line-strong"}`} />
              {org.slack_connected ? t("team_slack_connected") : t("team_slack_not_connected")}
            </span>
            <p className="text-xs text-muted">{t("team_slack_hint")}</p>
            <div className="flex gap-2">
              <input className={inputClass} type="password" placeholder="https://hooks.slack.com/services/..." value={slackWebhook} onChange={(e) => setSlackWebhook(e.target.value)} />
              <button type="submit" className={primaryButton + " shrink-0"}>
                {t("settings_save")}
              </button>
            </div>
            {org.slack_connected && (
              <div className="flex gap-2">
                <button type="button" className={secondaryButton} onClick={() => run(() => hub.testSlack(), t("team_slack_test_done"))}>
                  {t("team_slack_test")}
                </button>
                <button type="button" className={secondaryButton} onClick={() => run(() => hub.setSlack(""), t("team_saved"))}>
                  {t("team_disconnect")}
                </button>
              </div>
            )}
          </form>
        </Section>
      )}

      {audit && (
        <Section title={t("team_audit")} description={t("team_audit_hint")}>
          <div className={`${cardClass} max-h-[420px] overflow-auto p-0`}>
            <table className="w-full text-[13px]">
              <thead className="sticky top-0 bg-surface text-left text-xs text-muted">
                <tr className="border-b border-line">
                  <th className="px-3 py-2 font-semibold">{t("team_when")}</th>
                  <th className="px-3 py-2 font-semibold">{t("team_who")}</th>
                  <th className="px-3 py-2 font-semibold">{t("sidebar_project")}</th>
                  <th className="px-3 py-2 font-semibold">{t("team_what")}</th>
                </tr>
              </thead>
              <tbody>
                {audit.map((ev) => (
                  <tr key={ev.id} className="border-b border-line last:border-0">
                    <td className="whitespace-nowrap px-3 py-1.5 text-ink-2">{dateTime(ev.ts)}</td>
                    <td className="px-3 py-1.5">{ev.user_name ?? "—"}</td>
                    <td className="px-3 py-1.5 font-mono text-xs">{ev.project_id ?? ""}</td>
                    <td className="px-3 py-1.5">
                      {ev.action}
                      {ev.detail && <span className="text-ink-2"> · {ev.detail}</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      )}
    </div>
  );
}
