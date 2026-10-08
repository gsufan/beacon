import type {
  AIProviderConfig,
  AIStatus,
  AppConfigResponse,
  BrowseDirsResponse,
  DocContent,
  DocsTreeResponse,
  Project,
  ProjectCreatePayload,
  ProjectEntry,
  QueryResponse,
  SystemProvider,
  SyncStatus,
} from "./types";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

const API_KEY_STORAGE_KEY = "beacon:apiKey";

export function getApiKey(): string {
  return localStorage.getItem(API_KEY_STORAGE_KEY) ?? "";
}

export function setApiKey(key: string) {
  if (key) localStorage.setItem(API_KEY_STORAGE_KEY, key);
  else localStorage.removeItem(API_KEY_STORAGE_KEY);
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const apiKey = getApiKey();
  const res = await fetch(path, {
    headers: {
      "Content-Type": "application/json",
      ...(apiKey ? { "X-API-Key": apiKey } : {}),
    },
    ...init,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new ApiError(res.status, body.detail ?? res.statusText);
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

export const api = {
  listProjects: () => request<Project[]>("/projects"),

  health: (projectId: string) =>
    request<{ status: string; total_chunks_indexados: number }>(`/projects/${projectId}/health`),

  query: (projectId: string, question: string, topK = 5, language = "es") =>
    request<QueryResponse>(`/projects/${projectId}/query`, {
      method: "POST",
      body: JSON.stringify({ question, top_k: topK, language }),
    }),

  docsTree: (projectId: string) => request<DocsTreeResponse>(`/projects/${projectId}/docs/tree`),

  doc: (projectId: string, filePath: string) =>
    request<DocContent>(`/projects/${projectId}/docs?file_path=${encodeURIComponent(filePath)}`),

  getConfig: () => request<AppConfigResponse>("/config"),

  updateAIProvider: (cfg: AIProviderConfig) =>
    request<AIProviderConfig>("/config/ai-provider", {
      method: "PUT",
      body: JSON.stringify(cfg),
    }),

  createProject: (payload: ProjectCreatePayload) =>
    request<ProjectEntry>("/projects", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  deleteProject: (projectId: string, purgeData = false) =>
    request<{ status: string }>(`/projects/${projectId}?purge_data=${purgeData}`, {
      method: "DELETE",
    }),

  setAutoWatch: (projectId: string, enabled: boolean) =>
    request<{ id: string; auto_watch: boolean }>(`/projects/${projectId}/auto-watch`, {
      method: "PUT",
      body: JSON.stringify({ enabled }),
    }),

  triggerSync: (projectId: string) =>
    request<{ status: string }>(`/projects/${projectId}/sync`, { method: "POST" }),

  syncStatus: (projectId: string) => request<SyncStatus>(`/projects/${projectId}/sync-status`),

  aiStatus: () => request<AIStatus>("/system/ai-status"),

  systemProviders: () => request<{ providers: SystemProvider[] }>("/system/providers"),

  availableModels: (ollamaHost?: string) => {
    const qs = ollamaHost ? `?ollama_host=${encodeURIComponent(ollamaHost)}` : "";
    return request<{ models: string[] }>(`/system/available-models${qs}`);
  },

  browseDirs: (path?: string) => {
    const qs = path ? `?path=${encodeURIComponent(path)}` : "";
    return request<BrowseDirsResponse>(`/system/browse-dirs${qs}`);
  },
};
