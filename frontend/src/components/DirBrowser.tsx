import { Folder, FolderUp } from "lucide-react";
import { useEffect, useState } from "react";
import { useLanguage } from "./LanguageSelector";
import { api } from "../lib/api";

interface Props {
  value: string;
  onChange: (path: string) => void;
}

/** Explora el filesystem del SERVIDOR donde corre Beacon (no el del navegador). */
export default function DirBrowser({ value, onChange }: Props) {
  const { t } = useLanguage();
  const [path, setPath] = useState(value);
  const [parent, setParent] = useState<string | null>(null);
  const [dirs, setDirs] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = (target?: string) => {
    api
      .browseDirs(target)
      .then((res) => {
        setPath(res.path);
        setParent(res.parent);
        setDirs(res.directories);
        setError(null);
        onChange(res.path);
      })
      .catch((e) => setError(e.message ?? t("settings_error_generic")));
  };

  useEffect(() => {
    load(value || undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="flex flex-col gap-2 rounded-lg border border-line p-2">
      <div className="flex items-center justify-between gap-2">
        <span className="truncate font-mono text-xs text-ink-2">{path}</span>
        {parent && (
          <button
            onClick={() => load(parent)}
            className="flex shrink-0 items-center gap-1 rounded-md px-2 py-1 text-xs text-muted hover:bg-surface-2"
          >
            <FolderUp size={14} /> {t("dirbrowser_up")}
          </button>
        )}
      </div>
      {error && <p className="text-xs text-danger-ink">{error}</p>}
      <div className="flex max-h-40 flex-col gap-0.5 overflow-y-auto">
        {dirs.map((d) => (
          <button
            key={d}
            onClick={() => load(`${path}/${d}`)}
            className="flex items-center gap-1.5 rounded-md px-2 py-1 text-left text-xs text-ink-2 hover:bg-surface-2"
          >
            <Folder size={14} /> {d}
          </button>
        ))}
        {dirs.length === 0 && <p className="px-2 py-1 text-xs text-muted">{t("dirbrowser_empty")}</p>}
      </div>
    </div>
  );
}
