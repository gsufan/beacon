import { useLanguage } from "./LanguageSelector";
import type { SourceItem } from "../lib/types";

export default function SourceCitation({ source }: { source: SourceItem }) {
  const { t } = useLanguage();
  return (
    <div className="flex items-start justify-between gap-3 rounded-xl border border-beacon-11 bg-white px-3.5 py-2.5 text-sm dark:border-beacon-6 dark:bg-beacon-4">
      <div>
        <div className="font-mono text-beacon-4 dark:text-beacon-11">
          {source.file_path}
          <span className="text-stone-400"> ({source.start_line}-{source.end_line})</span>
        </div>
        <div className="text-xs text-stone-500">
          {source.chunk_type} {source.name && `· ${source.name}`}
        </div>
      </div>
      {source.expanded ? (
        <span className="whitespace-nowrap rounded-full bg-beacon-11 px-2 py-0.5 text-xs text-beacon-9 dark:bg-beacon-9/15 dark:text-beacon-10">
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
