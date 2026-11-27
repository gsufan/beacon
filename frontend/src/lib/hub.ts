// Calls to Beacon Hub, through Beacon Local's /hub proxy (the browser only talks to Beacon Local).
import { request } from "./api";
import type { StoredSession } from "./session";
import type { QueryResponse } from "./types";

export type Role = "product_owner" | "scrum_master" | "developer";
export const ROLES: Role[] = ["product_owner", "scrum_master", "developer"];

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
  startIssue: (projectId: string, ref: string, language: string) =>
    request<StartIssueResponse>(`/projects/${projectId}/issues/${encodeURIComponent(ref)}/start`, { method: "POST", ...json({ language }) }),
};
