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
  default_host?: string;
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
  code?: string;
}

export interface VerifiedMatch {
  id: string;
  question: string;
  answer: string;
  reviewer: string;
  ts: number;
  corrected: boolean;
  similarity: number;
  stale_files: string[];
}

export interface QueryResponse {
  answer: string;
  sources: SourceItem[];
  verified?: VerifiedMatch | null;
}

export interface DocsTreeResponse {
  files: string[];
  reviews?: Record<string, string>;
}

export interface DocReviewInfo {
  status: "draft" | "verified" | "pending";
  reviewer?: string;
  ts?: number;
  corrected?: boolean;
  new_draft?: boolean;
}

export interface DocContent {
  file_path: string;
  content_markdown: string;
  review?: DocReviewInfo;
  draft_markdown?: string | null;
}

export interface AIProviderConfig {
  provider: string;
  host: string;
  embedding_model: string;
  llm_model: string;
}

export interface AIStatus {
  reachable: boolean;
  llm_model_available: boolean;
  embedding_model_available: boolean;
}

export interface AppConfigResponse {
  ai_provider: AIProviderConfig;
  projects: ProjectEntry[];
}

export interface StorageProject {
  id: string;
  name: string;
  source_type: string;
  clone_bytes: number;
  index_bytes: number;
  docs_bytes: number;
  total_bytes: number;
  managed_clone: boolean;
}

export interface StorageResponse {
  projects: StorageProject[];
  total_bytes: number;
}

export interface SyncStatus {
  status: "idle" | "running" | "done" | "error";
  detail: unknown;
}
