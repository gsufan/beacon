// Calls to Beacon Hub, through Beacon Local's /hub proxy (the browser only talks to Beacon Local).
import { request } from "./api";
import type { StoredSession } from "./session";
import type { QueryResponse } from "./types";

export type Role = "product_owner" | "scrum_master" | "tech_lead" | "developer";
export const ROLES: Role[] = ["product_owner", "scrum_master", "tech_lead", "developer"];

export interface Org {
  id: number;
  name: string;
  plan: string;
  plan_label: string;
  seats: number;
  seats_used: number;
  period_end: number;
  days_left: number;
  expired: boolean;
  github_connected: boolean;
  jira_connected: boolean;
  jira_site: string | null;
  slack_connected: boolean;
}

export interface Plan {
  id: string;
  label: string;
  seats: number;
  price_per_user: number;
}

export interface TeamUser {
  id: number;
  name: string;
  email?: string;
  is_admin?: number;
  github_login: string | null;
  jira_user?: string | null;
  active: number;
}

export interface Member {
  id: number;
  name: string;
  github_login: string | null;
  role: Role;
}

export interface HubProject {
  id: string;
  name: string;
  github_repo: string | null;
  jira_project: string | null;
  role: Role | "admin" | null;
  members: Member[];
}

export interface Invitation {
  code: string;
  email: string;
  created: number;
}

export interface AuditEvent {
  id: number;
  user_name: string | null;
  project_id: string | null;
  action: string;
  detail: string | null;
  source: "hub" | "local";
  ts: number;
}

export interface Issue {
  id: string;
  ref: string;
  source: "github" | "jira";
  number?: number;
  title: string;
  body: string;
  state: string;
  url: string;
  author: string | null;
  assignees: string[];
  labels: string[];
  updated_at: string | null;
  comments: number;
}

export interface Commit {
  sha: string;
  message: string;
  author: string;
  date: string;
  url: string;
  issues: number[];
}

export interface StartIssueResponse extends QueryResponse {
  issue: { id: string; ref: string; source: string; title: string; url: string; labels: string[] };
}

export interface IssuesResponse {
  source: "github" | "jira";
  where: string;
  issues: Issue[];
  needs_login?: boolean;
  person?: string | null;
}

export interface Experts {
  files: { path: string; commits: number; authors: { name: string; commits: number }[] }[];
  overall: { name: string; commits: number; last: string | null }[];
}

export interface InstanceStatus {
  registered: boolean;
  revoked: boolean;
  lease_exp: number | null;
  blocked: string | null;
}

export interface Installation {
  id: string;
  name: string;
  user_id: number | null;
  user_name: string | null;
  created: number;
  last_seen: number | null;
  revoked: number;
  wiped: number;
  version: string | null;
}

export interface IssueCode {
  ref: string;
  commits: { sha: string; author: string; date: string; message: string; files: string[] }[];
  files: { path: string; commits: number }[];
}

export interface RelatedIssue {
  similarity: number;
  id: string;
  ref: string;
  title: string;
  url: string;
  state: string;
  source: string;
}

export interface ImpactRef {
  name: string;
  file_path: string;
  start_line: number;
  end_line: number;
  via?: string;
}

export interface Impact {
  targets: { target: string; definitions: ImpactRef[]; callers: ImpactRef[]; indirect: ImpactRef[]; tests: ImpactRef[]; untested: boolean }[];
  files: string[];
  experts?: { name: string; commits: number }[];
  docs?: { file_path: string; status: string; reviewer?: string }[];
}

export interface KnowledgeModule {
  module: string;
  files: number;
  commits: number;
  people: number;
  bus_factor: number;
  risk: "alto" | "medio" | "bajo";
  tests: number;
  experts: { name: string; commits: number }[];
  last: string | null;
}

export interface KnowledgeMap {
  modules: KnowledgeModule[];
  project_bus_factor: number;
  people: number;
}

export interface SprintSummary {
  days: number;
  commits: number;
  authors: { name: string; commits: number }[];
  modules: { module: string; changes: number }[];
  issues: { issue: string; commits: { sha: string; author: string; date: string; message: string }[] }[];
}

export interface InsightRow {
  module: string;
  questions: number;
  no_info: number;
  no_info_rate: number;
  docs: number;
  verified: number;
  verified_rate: number;
}

export interface Insights {
  questions: number;
  no_info: number;
  gaps: InsightRow[];
  radar: InsightRow[];
}

export interface VerifiedAnswerItem {
  id: string;
  question: string;
  answer: string;
  reviewer: string;
  ts: number;
  corrected: boolean;
  valid: boolean;
  stale_files: string[];
}

const json = (body: unknown) => ({ body: JSON.stringify(body) });

export const hub = {
  status: () =>
    request<{ mode: "team" | "standalone"; hub_url: string | null; edition?: string; instance?: InstanceStatus }>("/auth/status"),
  registerInstance: (name: string, personal: boolean) =>
    request<{ status: string; instance_id: string }>("/auth/instance", { method: "POST", ...json({ name, personal }) }),
  instances: () => request<{ instances: Installation[] }>("/hub/instances"),
  revokeInstance: (id: string) => request<{ status: string }>(`/hub/instances/${id}/revoke`, { method: "POST" }),
  login: (email: string, password: string) =>
    request<StoredSession>("/hub/auth/login", { method: "POST", ...json({ email, password }) }),
  signup: (body: { org_name: string; name: string; email: string; password: string }) =>
    request<StoredSession>("/hub/signup", { method: "POST", ...json(body) }),
  acceptInvitation: (body: { code: string; name: string; password: string }) =>
    request<StoredSession>("/hub/invitations/accept", { method: "POST", ...json(body) }),
  refresh: () => request<StoredSession>("/hub/auth/refresh", { method: "POST" }),

  org: () => request<Org>("/hub/org"),
  plans: () => request<{ plans: Plan[]; trial_days: number }>("/hub/plans"),
  choosePlan: (plan: string) => request<StoredSession>("/hub/org/plan", { method: "POST", ...json({ plan }) }),
  setGitHubToken: (token: string) => request<{ github_connected: boolean }>("/hub/org/github", { method: "PUT", ...json({ token }) }),
  setJira: (site: string, email: string, token: string) =>
    request<{ jira_connected: boolean }>("/hub/org/jira", { method: "PUT", ...json({ site, email, token }) }),
  setSlack: (webhook: string) => request<{ slack_connected: boolean }>("/hub/org/slack", { method: "PUT", ...json({ webhook }) }),
  testSlack: () => request<{ status: string }>("/hub/org/slack/test", { method: "POST" }),

  users: () => request<{ users: TeamUser[] }>("/hub/users"),
  patchUser: (id: number, body: Partial<{ github_login: string; jira_user: string; is_admin: boolean; active: boolean }>) =>
    request<{ status: string }>(`/hub/users/${id}`, { method: "PATCH", ...json(body) }),
  invitations: () => request<{ invitations: Invitation[] }>("/hub/invitations"),
  invite: (email: string) => request<{ code: string; email: string }>("/hub/invitations", { method: "POST", ...json({ email }) }),
  revokeInvitation: (code: string) => request<{ status: string }>(`/hub/invitations/${code}`, { method: "DELETE" }),

  projects: () => request<{ projects: HubProject[] }>("/hub/projects"),
  createProject: (body: { id: string; name: string; github_repo?: string; jira_project?: string }) =>
    request<HubProject>("/hub/projects", { method: "POST", ...json(body) }),
  patchProject: (id: string, body: { github_repo?: string; jira_project?: string; name?: string }) =>
    request<{ status: string }>(`/hub/projects/${id}`, { method: "PATCH", ...json(body) }),
  deleteProject: (id: string) => request<{ status: string }>(`/hub/projects/${id}`, { method: "DELETE" }),
  setMember: (projectId: string, userId: number, role: Role) =>
    request<{ status: string }>(`/hub/projects/${projectId}/members/${userId}`, { method: "PUT", ...json({ role }) }),
  removeMember: (projectId: string, userId: number) =>
    request<{ status: string }>(`/hub/projects/${projectId}/members/${userId}`, { method: "DELETE" }),

  audit: (projectId?: string) =>
    request<{ events: AuditEvent[] }>(`/hub/audit${projectId ? `?project_id=${encodeURIComponent(projectId)}` : ""}`),

  issues: (projectId: string, filter: "mine" | "all" | number) => {
    const qs = filter === "mine" ? "?mine=true" : filter === "all" ? "" : `?assignee_id=${filter}`;
    return request<IssuesResponse>(`/hub/projects/${projectId}/issues${qs}`);
  },
  activity: (projectId: string, assigneeId?: number) =>
    request<{ repo: string; commits: Commit[]; needs_login?: boolean }>(
      `/hub/projects/${projectId}/github/activity${assigneeId !== undefined ? `?assignee_id=${assigneeId}` : ""}`,
    ),
  comment: (projectId: string, ref: string, body: string) =>
    request<{ url: string }>(`/hub/projects/${projectId}/issues/${encodeURIComponent(ref)}/comment`, { method: "POST", ...json({ body }) }),
  shareSlack: (projectId: string, text: string) =>
    request<{ status: string }>(`/hub/projects/${projectId}/slack`, { method: "POST", ...json({ text }) }),
  experts: (projectId: string, paths: string[]) =>
    request<Experts>(`/projects/${projectId}/experts?${paths.map((p) => `path=${encodeURIComponent(p)}`).join("&")}`),
  closedIssues: (projectId: string) => request<IssuesResponse>(`/hub/projects/${projectId}/issues?state=closed`),
  planIssue: (projectId: string, ref: string, language: string) =>
    request<StartIssueResponse>(`/projects/${projectId}/issues/${encodeURIComponent(ref)}/plan`, { method: "POST", ...json({ language }) }),
  issueCode: (projectId: string, ref: string) => request<IssueCode>(`/projects/${projectId}/issues/${encodeURIComponent(ref)}/code`),
  related: (projectId: string, issue: Issue, candidates: Issue[]) =>
    request<{ related: RelatedIssue[] }>(`/projects/${projectId}/issues/related`, { method: "POST", ...json({ issue, candidates }) }),
  impact: (projectId: string, targets: string[]) => request<Impact>(`/projects/${projectId}/impact`, { method: "POST", ...json({ targets }) }),
  qaPlan: (projectId: string, targets: string[], issue: Issue | null, language: string) =>
    request<{ impact: Impact; plan: string }>(`/projects/${projectId}/qa-plan`, { method: "POST", ...json({ targets, issue, language }) }),
  handover: (projectId: string, body: { person: string; git_author?: string; issues: Issue[]; commits: Commit[]; language: string }) =>
    request<{ summary: string; local_commits: { date: string; message: string; files: string[] }[] }>(`/projects/${projectId}/handover`, {
      method: "POST",
      ...json(body),
    }),
  knowledge: (projectId: string) => request<KnowledgeMap>(`/projects/${projectId}/knowledge`),
  summary: (projectId: string, days: number) => request<SprintSummary>(`/projects/${projectId}/summary?days=${days}`),
  insights: (projectId: string) => request<Insights>(`/projects/${projectId}/insights`),
  verifiedAnswers: (projectId: string) => request<{ answers: VerifiedAnswerItem[] }>(`/projects/${projectId}/answers`),
  deleteVerifiedAnswer: (projectId: string, id: string) => request<{ status: string }>(`/projects/${projectId}/answers/${id}`, { method: "DELETE" }),
  startIssue: (projectId: string, ref: string, language: string) =>
    request<StartIssueResponse>(`/projects/${projectId}/issues/${encodeURIComponent(ref)}/start`, { method: "POST", ...json({ language }) }),
};
