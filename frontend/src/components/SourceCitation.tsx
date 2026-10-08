import { useState } from "react";
import { useLanguage } from "./LanguageSelector";
import type { SourceItem } from "../lib/types";

export default function SourceCitation({
  source,
  defaultOpen = false,
}: {
  source: SourceItem;
  defaultOpen?: boolean;
}) {
  const { t } = useLanguage();
  const [open, setOpen] = useState(defaultOpen);
  // Cosine distance: 0 = identical, so similarity is its complement.
  const similarity = Math.min(1, Math.max(0, 1 - source.distance));

  return (
    <div
      className={`flex flex-col gap-1.5 rounded-xl px-3.5 py-3 text-sm ${
        source.expanded
          ? "border border-dashed border-graph-line bg-graph-bg"
          : "border border-line bg-surface"
      }`}
    >
      <div className="flex items-center justify-between gap-2">
        {source.name ? (
          <span className="min-w-0 break-words font-mono text-[13px] font-medium text-ink">{source.name}</span>
        ) : (
          <span />
        )}
        <span
          className={`shrink-0 rounded-full px-2 py-px text-[11px] ${
            source.expanded ? "bg-graph-chip text-graph-ink" : "bg-surface-2 text-ink-2"
          }`}
        >
          {source.chunk_type}
        </span>
      </div>

      <div className="flex flex-wrap gap-x-1.5 font-mono text-xs text-muted">
        <span className="break-all">{source.file_path}</span>
        <span>
          ({source.start_line}-{source.end_line})
        </span>
      </div>

      {open && source.code && (
        <pre className="max-h-64 overflow-auto rounded-lg bg-code-bg px-3 py-2.5 font-mono text-xs leading-relaxed text-code-ink">
          {source.code}
        </pre>
      )}

      <div className="flex items-center gap-2">
        {source.expanded ? (
          <span className="whitespace-nowrap text-xs font-medium text-graph-ink">{t("chat_call_graph_badge")}</span>
        ) : (
          <>
            <div className="h-1 flex-1 rounded-full bg-accent-tint" aria-hidden="true">
              <div className="h-1 rounded-full bg-accent" style={{ width: `${similarity * 100}%` }} />
            </div>
            <span className="whitespace-nowrap font-mono text-[11px] text-muted">
              {t("chat_distance")} {source.distance.toFixed(3)}
            </span>
          </>
        )}
        {source.code && (
          <button
            type="button"
            onClick={() => setOpen(!open)}
            aria-expanded={open}
            className="ml-auto whitespace-nowrap text-xs font-medium text-accent-ink hover:underline"
          >
            {open ? t("sources_hide_code") : t("sources_show_code")}
          </button>
        )}
      </div>
    </div>
  );
}
