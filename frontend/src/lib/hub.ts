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
  number: number;
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
  issue: { number: number; title: string; url: string; labels: string[] };
}

const json = (body: unknown) => ({ body: JSON.stringify(body) });

export const hub = {
  status: () => request<{ mode: "team" | "standalone"; hub_url: string | null }>("/auth/status"),
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

  users: () => request<{ users: TeamUser[] }>("/hub/users"),
  patchUser: (id: number, body: Partial<{ github_login: string; is_admin: boolean; active: boolean }>) =>
    request<{ status: string }>(`/hub/users/${id}`, { method: "PATCH", ...json(body) }),
  invitations: () => request<{ invitations: Invitation[] }>("/hub/invitations"),
  invite: (email: string) => request<{ code: string; email: string }>("/hub/invitations", { method: "POST", ...json({ email }) }),
  revokeInvitation: (code: string) => request<{ status: string }>(`/hub/invitations/${code}`, { method: "DELETE" }),

  projects: () => request<{ projects: HubProject[] }>("/hub/projects"),
  createProject: (body: { id: string; name: string; github_repo?: string }) =>
    request<HubProject>("/hub/projects", { method: "POST", ...json(body) }),
  patchProject: (id: string, body: { github_repo?: string; name?: string }) =>
    request<{ status: string }>(`/hub/projects/${id}`, { method: "PATCH", ...json(body) }),
  deleteProject: (id: string) => request<{ status: string }>(`/hub/projects/${id}`, { method: "DELETE" }),
  setMember: (projectId: string, userId: number, role: Role) =>
    request<{ status: string }>(`/hub/projects/${projectId}/members/${userId}`, { method: "PUT", ...json({ role }) }),
  removeMember: (projectId: string, userId: number) =>
    request<{ status: string }>(`/hub/projects/${projectId}/members/${userId}`, { method: "DELETE" }),

  audit: (projectId?: string) =>
    request<{ events: AuditEvent[] }>(`/hub/audit${projectId ? `?project_id=${encodeURIComponent(projectId)}` : ""}`),

  issues: (projectId: string, mine: boolean) =>
    request<{ repo: string; issues: Issue[]; needs_github_login?: boolean }>(
      `/hub/projects/${projectId}/github/issues?mine=${mine}`,
    ),
  activity: (projectId: string) => request<{ repo: string; commits: Commit[] }>(`/hub/projects/${projectId}/github/activity`),
  startIssue: (projectId: string, number: number, language: string) =>
    request<StartIssueResponse>(`/projects/${projectId}/issues/${number}/start`, { method: "POST", ...json({ language }) }),
};
