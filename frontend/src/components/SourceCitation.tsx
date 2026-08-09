import { useLanguage } from "./LanguageSelector";
import type { SourceItem } from "../lib/types";

export default function SourceCitation({ source }: { source: SourceItem }) {
  const { t } = useLanguage();
  return (
    <div className="flex items-start justify-between gap-3 rounded-xl border border-stone-200 bg-white px-3.5 py-2.5 text-sm dark:border-stone-700 dark:bg-stone-800">
      <div>
        <div className="font-mono text-stone-800 dark:text-stone-200">
          {source.file_path}
          <span className="text-stone-400"> ({source.start_line}-{source.end_line})</span>
        </div>
        <div className="text-xs text-stone-500">
          {source.chunk_type} {source.name && `· ${source.name}`}
        </div>
      </div>
      {source.expanded ? (
        <span className="whitespace-nowrap rounded-full bg-amber-50 px-2 py-0.5 text-xs text-amber-700 dark:bg-amber-400/10 dark:text-amber-400">
          {t("chat_call_graph_badge")}
        </span>
      ) : (
        <span className="whitespace-nowrap text-xs text-stone-400">
          {t("chat_distance")} {source.distance.toFixed(3)}
        </span>
      )}
    </div>
  );
}
