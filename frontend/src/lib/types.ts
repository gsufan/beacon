export interface Project {
  id: string;
  name: string;
}

export interface ProjectEntry extends Project {
  repo_path: string;
  source_type: "local" | "git";
  repo_url?: string | null;
  auto_watch: boolean;
}

export interface ProjectCreatePayload {
  id: string;
  name: string;
  source_type: "local" | "git";
  repo_path?: string;
  repo_url?: string;
  auth_token?: string;
}

export interface SystemProvider {
  id: string;
  name: string;
}

export interface BrowseDirsResponse {
  path: string;
  parent: string | null;
  directories: string[];
}

export interface SourceItem {
  file_path: string;
  chunk_type: string;
  name: string;
  start_line: number;
  end_line: number;
  distance: number;
  expanded: boolean;
}

export interface QueryResponse {
  answer: string;
  sources: SourceItem[];
}

export interface DocsTreeResponse {
  files: string[];
}

export interface DocContent {
  file_path: string;
  content_markdown: string;
}

export interface AIProviderConfig {
  provider: string;
  ollama_host: string;
  embedding_model: string;
  llm_model: string;
}

export interface AppConfigResponse {
  ai_provider: AIProviderConfig;
  projects: ProjectEntry[];
}

export interface SyncStatus {
  status: "idle" | "running" | "done" | "error";
  detail: unknown;
}
