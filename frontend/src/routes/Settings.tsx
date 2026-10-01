import { useEffect, useRef, useState } from "react";
import DirBrowser from "../components/DirBrowser";
import Toggle from "../components/Toggle";
import { useLanguage } from "../components/LanguageSelector";
import { useProject } from "../components/ProjectSelector";
import { api, ApiError, getApiKey, setApiKey } from "../lib/api";
import type { AIProviderConfig, ProjectCreatePayload, ProjectEntry, SyncStatus, SystemProvider } from "../lib/types";

export default function Settings() {
  const { reloadProjects, setProjectId } = useProject();
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

  const [syncingId, setSyncingId] = useState<string | null>(null);
  const [syncStatus, setSyncStatus] = useState<SyncStatus | null>(null);
  const pollRef = useRef<number | null>(null);

  const reloadConfig = () => {
    api.getConfig().then((cfg) => {
      setAiConfig(cfg.ai_provider);
      setProjectEntries(cfg.projects);
    });
  };

  useEffect(() => {
    reloadConfig();
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
      if (pollRef.current) window.clearInterval(pollRef.current);
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
    setSyncingId(id);
    setSyncStatus({ status: "running", detail: null });
    try {
      await api.triggerSync(id);
      if (pollRef.current) window.clearInterval(pollRef.current);
      pollRef.current = window.setInterval(async () => {
        const status = await api.syncStatus(id);
        setSyncStatus(status);
        if (status.status === "done" || status.status === "error") {
          if (pollRef.current) window.clearInterval(pollRef.current);
        }
      }, 2000);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : t("settings_error_generic"));
      setSyncingId(null);
    }
  };

  const inputClass =
    "rounded-lg border border-beacon-11 px-3 py-1.5 text-sm dark:border-beacon-6 dark:bg-beacon-4";
  const cardClass =
    "rounded-2xl border border-beacon-11 bg-white p-4 dark:border-beacon-6 dark:bg-beacon-4";

  return (
    <div className="mx-auto flex max-w-2xl flex-col gap-10">
      {message && <p className="text-sm text-green-600 dark:text-green-400">{message}</p>}
      {error && <p className="text-sm text-red-600 dark:text-red-400">{error}</p>}

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-semibold">{t("settings_security")}</h2>
        <div className={`flex flex-col gap-2 ${cardClass}`}>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-stone-500">{t("settings_api_key")}</span>
            <input
              type="password"
              placeholder={t("settings_api_key_placeholder")}
              value={apiKeyInput}
              onChange={(e) => setApiKeyInput(e.target.value)}
              className={inputClass}
            />
          </label>
          <p className="text-xs text-stone-400">{t("settings_api_key_hint")}</p>
          <button
            onClick={() => {
              setApiKey(apiKeyInput);
              window.location.reload();
            }}
            className="w-fit rounded-lg border border-beacon-11 px-4 py-2 text-sm font-medium hover:bg-beacon-11 dark:border-beacon-6 dark:hover:bg-beacon-4"
          >
            {t("settings_api_key_save")}
          </button>
        </div>
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-semibold">{t("settings_ai_provider")}</h2>
        {aiConfig && (
          <div className={`flex flex-col gap-3 ${cardClass}`}>
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-stone-500">{t("settings_provider")}</span>
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
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-stone-500">{t("settings_ollama_host")}</span>
              <input
                value={aiConfig.ollama_host}
                onChange={(e) => setAiConfig({ ...aiConfig, ollama_host: e.target.value })}
                className={inputClass}
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-stone-500">{t("settings_embedding_model")}</span>
              <select
                value={aiConfig.embedding_model}
                onChange={(e) => setAiConfig({ ...aiConfig, embedding_model: e.target.value })}
                className={inputClass}
              >
                {!models.includes(aiConfig.embedding_model) && (
                  <option value={aiConfig.embedding_model}>{aiConfig.embedding_model}</option>
                )}
                {models.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-stone-500">{t("settings_llm_model")}</span>
              <select
                value={aiConfig.llm_model}
                onChange={(e) => setAiConfig({ ...aiConfig, llm_model: e.target.value })}
                className={inputClass}
              >
                {!models.includes(aiConfig.llm_model) && (
                  <option value={aiConfig.llm_model}>{aiConfig.llm_model}</option>
                )}
                {models.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
            </label>
            {modelsLoaded && models.length === 0 && (
              <p className="text-xs text-stone-400">
                {t("settings_no_models")} '{aiConfig.ollama_host}'. {t("settings_no_models_hint")}
              </p>
            )}
            <button
              onClick={saveAiConfig}
              disabled={savingAi}
              className="w-fit rounded-lg bg-beacon-9 px-4 py-2 text-sm font-medium text-white hover:bg-beacon-8 disabled:opacity-40"
            >
              {savingAi ? t("settings_saving") : t("settings_save")}
            </button>
          </div>
        )}
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-semibold">{t("settings_projects")}</h2>
        <div className="flex flex-col gap-2">
          {projectEntries.map((entry) => (
            <div key={entry.id} className={`flex flex-col gap-2 ${cardClass}`}>
              <div className="flex items-center justify-between">
                <button className="text-left text-sm hover:underline" onClick={() => setProjectId(entry.id)}>
                  {entry.name} <span className="text-stone-400">({entry.id})</span>
                </button>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => startSync(entry.id)}
                    disabled={syncingId === entry.id && syncStatus?.status === "running"}
                    className="rounded-md border border-beacon-11 px-2 py-1 text-xs disabled:opacity-40 dark:border-beacon-6"
                  >
                    {t("settings_sync")}
                  </button>
                  <button
                    onClick={() => deleteProject(entry.id)}
                    className="rounded-md border border-red-200 px-2 py-1 text-xs text-red-600 dark:border-red-900 dark:text-red-400"
                  >
                    {t("settings_delete")}
                  </button>
                </div>
              </div>
              <div className="flex items-center justify-between text-xs text-stone-500">
                <span className="font-mono">
                  {entry.source_type === "git" ? entry.repo_url : entry.repo_path}
                </span>
                <label className="flex items-center gap-2">
                  {t("settings_auto_watch")}
                  <Toggle checked={entry.auto_watch} onChange={() => toggleAutoWatch(entry)} />
                </label>
              </div>
            </div>
          ))}
        </div>

        {syncingId && syncStatus && (
          <div className={`text-xs ${cardClass}`}>
            <p className="font-medium">
              {t("settings_sync_of")} '{syncingId}': {syncStatus.status}
            </p>
            {syncStatus.detail ? (
              <pre className="mt-1 overflow-auto whitespace-pre-wrap text-stone-500">
                {JSON.stringify(syncStatus.detail, null, 2)}
              </pre>
            ) : null}
          </div>
        )}

        <div className={`mt-2 flex flex-col gap-3 ${cardClass}`}>
          <h3 className="text-sm font-medium">{t("settings_register_project")}</h3>

          <div className="flex gap-1 rounded-lg bg-beacon-11 p-1 text-sm dark:bg-beacon-4">
            {(["local", "git"] as const).map((mode) => (
              <button
                key={mode}
                onClick={() => setSourceType(mode)}
                className={`flex-1 rounded-md py-1.5 ${
                  sourceType === mode
                    ? "bg-white shadow-sm dark:bg-beacon-2"
                    : "text-stone-500"
                }`}
              >
                {mode === "local" ? t("settings_mode_local") : t("settings_mode_git")}
              </button>
            ))}
          </div>

          <input
            placeholder={t("settings_id_placeholder")}
            value={newProject.id}
            onChange={(e) => setNewProject({ ...newProject, id: e.target.value })}
            className={inputClass}
          />
          <input
            placeholder={t("settings_name_placeholder")}
            value={newProject.name}
            onChange={(e) => setNewProject({ ...newProject, name: e.target.value })}
            className={inputClass}
          />

          {sourceType === "local" ? (
            <DirBrowser
              value={newProject.repo_path}
              onChange={(path) => setNewProject({ ...newProject, repo_path: path })}
            />
          ) : (
            <>
              <input
                placeholder={t("settings_url_placeholder")}
                value={newProject.repo_url}
                onChange={(e) => setNewProject({ ...newProject, repo_url: e.target.value })}
                className={inputClass}
              />
              <input
                placeholder={t("settings_token_placeholder")}
                type="password"
                value={newProject.auth_token}
                onChange={(e) => setNewProject({ ...newProject, auth_token: e.target.value })}
                className={inputClass}
              />
            </>
          )}

          <button
            onClick={createProject}
            disabled={
              creating ||
              !newProject.id ||
              !newProject.name ||
              (sourceType === "local" ? !newProject.repo_path : !newProject.repo_url)
            }
            className="w-fit rounded-lg bg-beacon-9 px-4 py-2 text-sm font-medium text-white hover:bg-beacon-8 disabled:opacity-40"
          >
            {creating ? t("settings_registering") : t("settings_register")}
          </button>
        </div>
      </section>
    </div>
  );
}
