import { useState } from "react";
import SourceCitation from "../components/SourceCitation";
import { useLanguage } from "../components/LanguageSelector";
import { useProject } from "../components/ProjectSelector";
import { api, ApiError } from "../lib/api";
import type { QueryResponse } from "../lib/types";

export default function Chat() {
  const { projectId } = useProject();
  const { language, t } = useLanguage();
  const [question, setQuestion] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<QueryResponse | null>(null);

  const ask = async () => {
    if (!projectId || question.trim().length < 3) return;
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const res = await api.query(projectId, question.trim(), 5, language);
      setResult(res);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : t("settings_error_generic"));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-4">
      <h1 className="text-xl font-semibold">{t("chat_title")}</h1>
      <textarea
        value={question}
        onChange={(e) => setQuestion(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) ask();
        }}
        rows={3}
        placeholder={t("chat_placeholder")}
        className="w-full resize-none rounded-2xl border border-stone-200 bg-white px-4 py-3 text-sm outline-none focus:border-amber-400 dark:border-stone-700 dark:bg-stone-800 dark:focus:border-amber-500"
      />
      <button
        onClick={ask}
        disabled={loading || !projectId}
        className="w-fit rounded-lg bg-amber-500 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-amber-600 disabled:opacity-40"
      >
        {loading ? t("chat_asking") : t("chat_ask")}
      </button>

      {error && <p className="text-sm text-red-600 dark:text-red-400">{error}</p>}

      {result && (
        <div className="flex flex-col gap-4">
          <div className="whitespace-pre-wrap rounded-2xl border border-stone-200 bg-white p-4 text-sm dark:border-stone-700 dark:bg-stone-800">
            {result.answer}
          </div>
          {result.sources.length > 0 && (
            <div className="flex flex-col gap-2">
              <h2 className="text-sm font-medium text-stone-500">{t("chat_sources")}</h2>
              {result.sources.map((s, i) => (
                <SourceCitation key={`${s.file_path}-${s.start_line}-${i}`} source={s} />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
