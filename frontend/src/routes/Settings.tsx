import { GitBranch, HardDrive } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import DirBrowser from "../components/DirBrowser";
import Toggle from "../components/Toggle";
import { useLanguage } from "../components/LanguageSelector";
import { useProject } from "../components/ProjectSelector";
import { api, ApiError, getApiKey, setApiKey } from "../lib/api";
import type { AIProviderConfig, ProjectCreatePayload, ProjectEntry, SyncStatus, SystemProvider } from "../lib/types";

// Ollama lists untagged models as "<name>:latest".
function isInstalled(model: string, installed: string[]) {
  return installed.includes(model) || installed.includes(`${model}:latest`);
}

function Section({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: React.ReactNode;
}) {
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

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex min-w-0 flex-col gap-1 text-sm">
      <span className="text-xs font-semibold text-muted">{label}</span>
      {children}
    </label>
  );
}

export default function Settings() {
  const { projectId, reloadProjects, setProjectId } = useProject();
  const { t } = useLanguage();
  const [apiKeyInput, setApiKeyInput] = useState(getApiKey());
  const [projectEntries, setProjectEntries] = useState<ProjectEntry[]>([]);
  const [aiConfig, setAiConfig] = useState<AIProviderConfig | null>(null);
  const [providers, setProviders] = useState<SystemProvider[]>([]);
  const [models, setModels] = useState<string[]>([]);
  const [modelsLoaded, setModelsLoaded] = useState(false);
  const [savingAi, setSavingAi] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [sourceType, setSourceType] = useState<"local" | "git">("local");
  const [newProject, setNewProject] = useState({
    id: "", name: "", repo_path: "", repo_url: "", auth_token: "",
  });
  const [creating, setCreating] = useState(false);

  // One status and one poller per project, so several syncs can run at once.
  const [syncById, setSyncById] = useState<Record<string, SyncStatus>>({});
  const pollsRef = useRef<Record<string, number>>({});
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);

  const reloadConfig = () => {
    api.getConfig().then((cfg) => {
      setAiConfig(cfg.ai_provider);
      setProjectEntries(cfg.projects);
    });
  };

  const stopPolling = (id: string) => {
    window.clearInterval(pollsRef.current[id]);
    delete pollsRef.current[id];
  };

  const pollSync = (id: string) => {
    stopPolling(id);
    pollsRef.current[id] = window.setInterval(async () => {
      try {
        const status = await api.syncStatus(id);
        setSyncById((prev) => ({ ...prev, [id]: status }));
        if (status.status !== "running") stopPolling(id);
      } catch {
        stopPolling(id);
      }
    }, 2000);
  };

  useEffect(() => {
    api.getConfig().then((cfg) => {
      setAiConfig(cfg.ai_provider);
      setProjectEntries(cfg.projects);
      // Pick up syncs that were already running (page reload, auto watcher).
      for (const project of cfg.projects) {
        api
          .syncStatus(project.id)
          .then((status) => {
            if (status.status !== "running") return;
            setSyncById((prev) => ({ ...prev, [project.id]: status }));
            pollSync(project.id);
          })
          .catch(() => {});
      }
    });
    api.systemProviders().then((res) => setProviders(res.providers));
  }, []);

  useEffect(() => {
    if (!aiConfig?.ollama_host) return;
    // El aviso de "no hay modelos" se muestra solo cuando la consulta terminó:
    // antes aparecía un instante en cada carga, mientras llegaba la respuesta.
    setModelsLoaded(false);
    api
      .availableModels(aiConfig.ollama_host)
      .then((res) => setModels(res.models))
      .catch(() => setModels([]))
      .finally(() => setModelsLoaded(true));
  }, [aiConfig?.ollama_host]);

  useEffect(() => {
    return () => {
      Object.keys(pollsRef.current).forEach(stopPolling);
    };
  }, []);

  const saveAiConfig = async () => {
    if (!aiConfig) return;
    setSavingAi(true);
    setError(null);
    setMessage(null);
    try {
      const updated = await api.updateAIProvider(aiConfig);
      setAiConfig(updated);
      setMessage(t("settings_ai_updated"));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : t("settings_error_generic"));
    } finally {
      setSavingAi(false);
    }
  };

  const createProject = async () => {
    setCreating(true);
    setError(null);
    setMessage(null);
    try {
      const payload: ProjectCreatePayload = {
        id: newProject.id, name: newProject.name, source_type: sourceType,
        ...(sourceType === "local"
          ? { repo_path: newProject.repo_path }
          : { repo_url: newProject.repo_url, auth_token: newProject.auth_token || undefined }),
      };
      await api.createProject(payload);
      setNewProject({ id: "", name: "", repo_path: "", repo_url: "", auth_token: "" });
      reloadProjects();
      reloadConfig();
      setMessage(`'${payload.id}' ${t("settings_registered")}`);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : t("settings_error_generic"));
    } finally {
      setCreating(false);
    }
  };

  const deleteProject = async (id: string) => {
    setError(null);
    setConfirmDeleteId(null);
    try {
      await api.deleteProject(id);
      reloadProjects();
      reloadConfig();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : t("settings_error_generic"));
    }
  };

  const toggleAutoWatch = async (project: ProjectEntry) => {
    try {
      await api.setAutoWatch(project.id, !project.auto_watch);
      reloadConfig();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : t("settings_error_generic"));
    }
  };

  const startSync = async (id: string) => {
    setError(null);
    setSyncById((prev) => ({ ...prev, [id]: { status: "running", detail: null } }));
    try {
      await api.triggerSync(id);
      pollSync(id);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : t("settings_error_generic"));
      setSyncById((prev) => {
        const next = { ...prev };
        delete next[id];
        return next;
      });
    }
  };

  const inputClass =
    "h-9 w-full rounded-lg border border-line-strong bg-surface px-3 text-sm outline-none placeholder:text-muted focus:border-accent";
  const cardClass = "rounded-[14px] border border-line bg-surface p-4";
  const primaryButton =
    "h-9 w-fit rounded-[9px] bg-accent px-4 text-sm font-semibold text-accent-on hover:opacity-90 disabled:opacity-40";
  const secondaryButton =
    "h-[30px] rounded-lg border border-line-strong bg-surface px-3 text-[13px] hover:bg-surface-2 disabled:opacity-40";
  const modelsKnown = modelsLoaded && models.length > 0;

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-8 px-10 pb-10 pt-6">
      <h1 className="font-display text-[22px] font-semibold">{t("nav_settings")}</h1>

      {message && (
        <p role="status" className="rounded-xl border border-line bg-surface px-4 py-3 text-sm">
          <span className="mr-2 inline-block h-2 w-2 rounded-full bg-ok" />
          {message}
        </p>
      )}
      {error && (
        <p role="alert" className="rounded-xl border border-danger-line bg-danger-bg px-4 py-3 text-sm text-danger-ink">
          {error}
        </p>
      )}

      <Section title={t("settings_projects")} description={t("settings_projects_desc")}>
        {projectEntries.length === 0 && <p className="text-sm text-muted">{t("settings_no_projects")}</p>}

        <div className="flex flex-col gap-2">
          {projectEntries.map((entry) => {
            const syncing = syncById[entry.id];
            const SourceIcon = entry.source_type === "git" ? GitBranch : HardDrive;
            return (
              <div key={entry.id} className={`flex flex-col gap-3 ${cardClass}`}>
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex min-w-0 flex-wrap items-center gap-2">
                    <span className="text-[15px] font-semibold">{entry.name}</span>
                    <span className="rounded-full bg-surface-2 px-2 py-px font-mono text-[11px] text-ink-2">{entry.id}</span>
                    {entry.id === projectId && (
                      <span className="rounded-full bg-accent-tint px-2 py-px text-[11px] font-semibold text-accent-ink">
                        {t("settings_in_use")}
                      </span>
                    )}
                  </div>
                  <div className="flex items-center gap-2">
                    {entry.id !== projectId && (
                      <button type="button" onClick={() => setProjectId(entry.id)} className={secondaryButton}>
                        {t("settings_use")}
                      </button>
                    )}
                    <button
                      type="button"
                      onClick={() => startSync(entry.id)}
                      disabled={syncing?.status === "running"}
                      className={secondaryButton}
                    >
                      {t("settings_sync")}
                    </button>
                    <button
                      type="button"
                      onClick={() => setConfirmDeleteId(entry.id)}
                      className="h-[30px] rounded-lg border border-danger-line px-3 text-[13px] text-danger-ink hover:bg-danger-bg"
                    >
                      {t("settings_delete")}
                    </button>
                  </div>
                </div>

                <div className="flex flex-wrap items-center justify-between gap-3 text-xs text-muted">
                  <span className="flex min-w-0 items-center gap-1.5">
                    <SourceIcon size={14} className="shrink-0" aria-hidden="true" />
                    <span className="shrink-0">
                      {entry.source_type === "git" ? t("settings_source_git") : t("settings_source_local")}
                    </span>
                    <span className="break-all font-mono text-ink-2">
                      {entry.source_type === "git" ? entry.repo_url : entry.repo_path}
                    </span>
                  </span>
                  <span className="flex shrink-0 items-center gap-2">
                    {t("settings_auto_watch")}
                    <Toggle
                      checked={entry.auto_watch}
                      onChange={() => toggleAutoWatch(entry)}
                      label={`${t("settings_auto_watch")}: ${entry.name}`}
                    />
                  </span>
                </div>

                {confirmDeleteId === entry.id && (
                  <div
                    role="alert"
                    className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-danger-line bg-danger-bg px-3 py-2 text-[13px]"
                  >
                    <span>{t("settings_delete_confirm", { id: entry.id })}</span>
                    <span className="flex gap-2">
                      <button
                        type="button"
                        onClick={() => deleteProject(entry.id)}
                        className="h-[30px] rounded-lg border border-danger-line bg-surface px-3 font-semibold text-danger-ink"
                      >
                        {t("settings_delete")}
                      </button>
                      <button type="button" onClick={() => setConfirmDeleteId(null)} className={secondaryButton}>
                        {t("settings_cancel")}
                      </button>
                    </span>
                  </div>
                )}

                {syncing && (
                  <div role="status" className="flex flex-col gap-1 rounded-lg bg-surface-2 px-3 py-2 text-[13px]">
                    <div className="flex items-center gap-2 font-semibold">
                      <span
                        className={`h-2 w-2 rounded-full ${
                          syncing.status === "error"
                            ? "bg-danger-ink"
                            : syncing.status === "running"
                              ? "animate-pulse bg-accent"
                              : "bg-ok"
                        }`}
                      />
                      {syncing.status === "error"
                        ? t("settings_sync_error")
                        : syncing.status === "running"
                          ? t("settings_sync_running")
                          : t("settings_sync_done")}
                    </div>
                    {typeof syncing.detail === "string" && <p className="text-ink-2">{syncing.detail}</p>}
                    {syncing.detail !== null && typeof syncing.detail === "object" && (
                      <details>
                        <summary className="cursor-pointer text-xs text-muted">{t("settings_sync_detail")}</summary>
                        <pre className="mt-1 overflow-auto whitespace-pre-wrap font-mono text-xs text-ink-2">
                          {JSON.stringify(syncing.detail, null, 2)}
                        </pre>
                      </details>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>

        <div className={`flex flex-col gap-3 ${cardClass}`}>
          <h3 className="text-sm font-semibold">{t("settings_register_project")}</h3>

          <div className="flex gap-1 rounded-[10px] border border-line bg-ground p-[3px] text-[13px]">
            {(["local", "git"] as const).map((mode) => (
              <button
                key={mode}
                type="button"
                onClick={() => setSourceType(mode)}
                aria-pressed={sourceType === mode}
                className={`h-[30px] flex-1 rounded-[7px] ${
                  sourceType === mode ? "bg-surface font-semibold shadow-sm" : "text-muted hover:text-ink"
                }`}
              >
                {mode === "local" ? t("settings_mode_local") : t("settings_mode_git")}
              </button>
            ))}
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            <Field label={t("settings_field_id")}>
              <input
                placeholder={t("settings_id_placeholder")}
                value={newProject.id}
                onChange={(e) => setNewProject({ ...newProject, id: e.target.value })}
                className={`${inputClass} font-mono`}
              />
            </Field>
            <Field label={t("settings_field_name")}>
              <input
                placeholder={t("settings_name_placeholder")}
                value={newProject.name}
                onChange={(e) => setNewProject({ ...newProject, name: e.target.value })}
                className={inputClass}
              />
            </Field>
          </div>

          {sourceType === "local" ? (
            <div className="flex flex-col gap-1">
              <span className="text-xs font-semibold text-muted">{t("settings_field_path")}</span>
              <DirBrowser
                value={newProject.repo_path}
                onChange={(path) => setNewProject({ ...newProject, repo_path: path })}
              />
            </div>
          ) : (
            <>
              <Field label={t("settings_field_url")}>
                <input
                  placeholder={t("settings_url_placeholder")}
                  value={newProject.repo_url}
                  onChange={(e) => setNewProject({ ...newProject, repo_url: e.target.value })}
                  className={inputClass}
                />
              </Field>
              <Field label={t("settings_field_token")}>
                <input
                  placeholder={t("settings_token_placeholder")}
                  type="password"
                  value={newProject.auth_token}
                  onChange={(e) => setNewProject({ ...newProject, auth_token: e.target.value })}
                  className={inputClass}
                />
              </Field>
            </>
          )}

          <button
            type="button"
            onClick={createProject}
            disabled={
              creating ||
              !newProject.id ||
              !newProject.name ||
              (sourceType === "local" ? !newProject.repo_path : !newProject.repo_url)
            }
            className={primaryButton}
          >
            {creating ? t("settings_registering") : t("settings_register")}
          </button>
        </div>
      </Section>

      <Section title={t("settings_ai_provider")} description={t("settings_ai_desc")}>
        {aiConfig && (
          <div className={`flex flex-col gap-3 ${cardClass}`}>
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label={t("settings_provider")}>
                <select
                  value={aiConfig.provider}
                  onChange={(e) => setAiConfig({ ...aiConfig, provider: e.target.value })}
                  className={inputClass}
                >
                  {providers.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label={t("settings_ollama_host")}>
                <input
                  value={aiConfig.ollama_host}
                  onChange={(e) => setAiConfig({ ...aiConfig, ollama_host: e.target.value })}
                  className={`${inputClass} font-mono`}
                />
              </Field>
            </div>

            {modelsLoaded && models.length === 0 && (
              <p role="status" className="rounded-lg border border-danger-line bg-danger-bg px-3 py-2 text-[13px] text-danger-ink">
                {t("settings_no_models")} '{aiConfig.ollama_host}'. {t("settings_no_models_hint")}
              </p>
            )}

            <div className="grid gap-3 sm:grid-cols-2">
              {(["embedding_model", "llm_model"] as const).map((key) => (
                <div key={key} className="flex min-w-0 flex-col gap-1">
                  <Field label={key === "embedding_model" ? t("settings_embedding_model") : t("settings_llm_model")}>
                    <select
                      value={aiConfig[key]}
                      onChange={(e) => setAiConfig({ ...aiConfig, [key]: e.target.value })}
                      className={`${inputClass} font-mono`}
                    >
                      {!models.includes(aiConfig[key]) && <option value={aiConfig[key]}>{aiConfig[key]}</option>}
                      {models.map((m) => (
                        <option key={m} value={m}>
                          {m}
                        </option>
                      ))}
                    </select>
                  </Field>
                  {modelsKnown && !isInstalled(aiConfig[key], models) && (
                    <p className="text-xs text-danger-ink">{t("settings_model_missing")}</p>
                  )}
                </div>
              ))}
            </div>

            <button type="button" onClick={saveAiConfig} disabled={savingAi} className={primaryButton}>
              {savingAi ? t("settings_saving") : t("settings_save")}
            </button>
          </div>
        )}
      </Section>

      <Section title={t("settings_security")}>
        <div className={`flex flex-col gap-3 ${cardClass}`}>
          <Field label={t("settings_api_key")}>
            <input
              type="password"
              placeholder={t("settings_api_key_placeholder")}
              value={apiKeyInput}
              onChange={(e) => setApiKeyInput(e.target.value)}
              className={inputClass}
            />
          </Field>
          <p className="text-xs text-muted">{t("settings_api_key_hint")}</p>
          <button
            type="button"
            onClick={() => {
              setApiKey(apiKeyInput);
              window.location.reload();
            }}
            className={`${secondaryButton} w-fit`}
          >
            {t("settings_api_key_save")}
          </button>
        </div>
      </Section>
    </div>
  );
}
