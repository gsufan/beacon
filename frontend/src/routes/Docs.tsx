import { useEffect, useState } from "react";
import MarkdownViewer from "../components/MarkdownViewer";
import { useLanguage } from "../components/LanguageSelector";
import { useProject } from "../components/ProjectSelector";
import { api, ApiError } from "../lib/api";

export default function Docs() {
  const { projectId } = useProject();
  const { t } = useLanguage();
  const [files, setFiles] = useState<string[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [content, setContent] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!projectId) return;
    setFiles([]);
    setSelected(null);
    setContent(null);
    api
      .docsTree(projectId)
      .then((res) => setFiles(res.files))
      .catch((e) => setError(e instanceof ApiError ? e.message : t("settings_error_generic")));
  }, [projectId]);

  useEffect(() => {
    if (!projectId || !selected) return;
    setContent(null);
    api
      .doc(projectId, selected)
      .then((res) => setContent(res.content_markdown))
      .catch((e) => setError(e instanceof ApiError ? e.message : t("settings_error_generic")));
  }, [projectId, selected]);

  return (
    <div className="grid grid-cols-[260px_1fr] gap-6">
      <aside className="flex flex-col gap-1 border-r border-stone-200 pr-4 dark:border-stone-700">
        <h2 className="mb-2 text-sm font-medium text-stone-500">{t("docs_title")}</h2>
        {files.length === 0 && <p className="text-sm text-stone-400">{t("docs_empty")}</p>}
        {files.map((f) => (
          <button
            key={f}
            onClick={() => setSelected(f)}
            className={`truncate rounded-lg px-2 py-1 text-left text-xs font-mono ${
              selected === f
                ? "bg-amber-50 text-amber-700 dark:bg-amber-400/10 dark:text-amber-400"
                : "text-stone-600 hover:bg-stone-100 dark:text-stone-400 dark:hover:bg-stone-700"
            }`}
            title={f}
          >
            {f}
          </button>
        ))}
      </aside>
      <section>
        {error && <p className="text-sm text-red-600 dark:text-red-400">{error}</p>}
        {!selected && !error && <p className="text-sm text-stone-400">{t("docs_select_file")}</p>}
        {content && (
          <div className="rounded-2xl border border-stone-200 bg-white p-6 dark:border-stone-700 dark:bg-stone-800">
            <MarkdownViewer content={content} />
          </div>
        )}
      </section>
    </div>
  );
}
