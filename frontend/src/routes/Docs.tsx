import { ChevronRight, FileText, Folder, FolderOpen } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router";
import MarkdownViewer from "../components/MarkdownViewer";
import { useLanguage } from "../components/LanguageSelector";
import { useProject } from "../components/ProjectSelector";
import { api, ApiError } from "../lib/api";

interface TreeNode {
  name: string;
  path: string;
  dirs: TreeNode[];
  files: string[];
}

function baseName(filePath: string) {
  return filePath.slice(filePath.lastIndexOf("/") + 1);
}

// Folders first, then files, both alphabetical; chains of single-child
// folders collapse into one row ("src/requests"), like code editors do.
function compact(node: TreeNode, isRoot: boolean): TreeNode {
  const dirs = node.dirs.map((d) => compact(d, false)).sort((a, b) => a.name.localeCompare(b.name));
  const files = [...node.files].sort((a, b) => a.localeCompare(b));
  if (!isRoot && dirs.length === 1 && files.length === 0) {
    return { ...dirs[0], name: `${node.name}/${dirs[0].name}` };
  }
  return { ...node, dirs, files };
}

function buildTree(files: string[]): TreeNode {
  const root: TreeNode = { name: "", path: "", dirs: [], files: [] };
  for (const file of files) {
    let node = root;
    for (const part of file.split("/").slice(0, -1)) {
      let child = node.dirs.find((d) => d.name === part);
      if (!child) {
        child = { name: part, path: node.path ? `${node.path}/${part}` : part, dirs: [], files: [] };
        node.dirs.push(child);
      }
      node = child;
    }
    node.files.push(file);
  }
  return compact(root, true);
}

interface TreeProps {
  node: TreeNode;
  depth: number;
  selected: string | null;
  isOpen: (path: string, depth: number) => boolean;
  onToggle: (path: string, depth: number) => void;
  onSelect: (file: string) => void;
}

function TreeLevel({ node, depth, selected, isOpen, onToggle, onSelect }: TreeProps) {
  return (
    <>
      {node.dirs.map((dir) => {
        const open = isOpen(dir.path, depth);
        const FolderIcon = open ? FolderOpen : Folder;
        return (
          <div key={dir.path} className="flex flex-col">
            <button
              type="button"
              onClick={() => onToggle(dir.path, depth)}
              aria-expanded={open}
              title={dir.path}
              className="flex h-7 items-center gap-1.5 rounded-md px-1 text-left text-[13px] text-ink hover:bg-surface-2"
            >
              <ChevronRight
                size={14}
                aria-hidden="true"
                className={`shrink-0 text-muted transition-transform ${open ? "rotate-90" : ""}`}
              />
              <FolderIcon size={14} aria-hidden="true" className="shrink-0 text-muted" />
              <span className="truncate font-mono">{dir.name}</span>
            </button>
            {open && (
              <div className="ml-[11px] flex flex-col border-l border-line pl-[7px]">
                <TreeLevel
                  node={dir}
                  depth={depth + 1}
                  selected={selected}
                  isOpen={isOpen}
                  onToggle={onToggle}
                  onSelect={onSelect}
                />
              </div>
            )}
          </div>
        );
      })}
      {node.files.map((file) => (
        <button
          key={file}
          type="button"
          onClick={() => onSelect(file)}
          aria-label={file}
          aria-current={selected === file ? "true" : undefined}
          title={file}
          className={`flex h-7 items-center gap-1.5 rounded-md pl-[23px] pr-1 text-left text-[13px] ${
            selected === file ? "bg-accent-tint font-medium text-accent-ink" : "text-ink-2 hover:bg-surface-2"
          }`}
        >
          <FileText size={14} aria-hidden="true" className="shrink-0 opacity-70" />
          <span className="truncate font-mono">{baseName(file)}</span>
        </button>
      ))}
    </>
  );
}

export default function Docs() {
  const { projectId } = useProject();
  const { t } = useLanguage();
  const [files, setFiles] = useState<string[]>([]);
  const [filter, setFilter] = useState("");
  // Folders the user opened or closed by hand; the rest follow the default.
  const [openOverrides, setOpenOverrides] = useState<Record<string, boolean>>({});
  const [selected, setSelected] = useState<string | null>(null);
  const [content, setContent] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!projectId) return;
    setFiles([]);
    setFilter("");
    setOpenOverrides({});
    setSelected(null);
    setContent(null);
    setError(null);
    api
      .docsTree(projectId)
      .then((res) => setFiles(res.files))
      .catch((e) => setError(e instanceof ApiError ? e.message : t("settings_error_generic")));
  }, [projectId]);

  useEffect(() => {
    if (!projectId || !selected) return;
    setContent(null);
    setError(null);
    api
      .doc(projectId, selected)
      .then((res) => setContent(res.content_markdown))
      .catch((e) => setError(e instanceof ApiError ? e.message : t("settings_error_generic")));
  }, [projectId, selected]);

  const needle = filter.trim().toLowerCase();
  const visibleFiles = useMemo(
    () => (needle ? files.filter((f) => f.toLowerCase().includes(needle)) : files),
    [files, needle]
  );
  const tree = useMemo(() => buildTree(visibleFiles), [visibleFiles]);

  // Top-level folders start open; while filtering, every match is shown.
  const isOpen = (path: string, depth: number) => (needle ? true : (openOverrides[path] ?? depth === 0));
  const toggleFolder = (path: string, depth: number) =>
    setOpenOverrides((prev) => ({ ...prev, [path]: !(prev[path] ?? depth === 0) }));

  const loadingDoc = selected !== null && content === null && !error;

  return (
    <div className="flex h-full flex-col gap-6 px-10 pb-7 pt-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="font-display text-[22px] font-semibold">{t("docs_heading")}</h1>
        {files.length > 0 && <span className="text-[13px] text-muted">{t("docs_count", { n: files.length })}</span>}
      </header>

      <div className="flex min-h-0 flex-1 flex-col gap-8 lg:flex-row">
        <aside className="flex min-h-0 w-full shrink-0 flex-col gap-3 lg:w-[300px]">
          <h2 className="text-xs font-semibold text-muted">{t("docs_title")}</h2>
          {files.length > 0 && (
            <input
              type="search"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder={t("docs_filter")}
              aria-label={t("docs_filter")}
              className="h-9 rounded-lg border border-line-strong bg-surface px-3 text-sm outline-none placeholder:text-muted focus:border-accent"
            />
          )}

          {files.length === 0 && !error && (
            <div className="flex flex-col gap-1 rounded-xl border border-dashed border-line-strong px-3.5 py-3">
              <p className="text-[13px] font-semibold">{t("docs_empty")}</p>
              <p className="text-[13px] text-ink-2">{t("docs_empty_hint")}</p>
              <Link to="/settings" className="w-fit text-[13px] font-semibold text-accent-ink hover:underline">
                {t("docs_empty_link")}
              </Link>
            </div>
          )}

          {files.length > 0 && visibleFiles.length === 0 && (
            <p className="text-[13px] text-muted">{t("docs_no_match")}</p>
          )}

          <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
            <TreeLevel
              node={tree}
              depth={0}
              selected={selected}
              isOpen={isOpen}
              onToggle={toggleFolder}
              onSelect={setSelected}
            />
          </div>
        </aside>

        <section className="min-h-0 min-w-0 flex-1 overflow-y-auto">
          {error && (
            <p role="alert" className="rounded-xl border border-danger-line bg-danger-bg px-4 py-3 text-sm text-danger-ink">
              {error}
            </p>
          )}

          {!selected && !error && (
            <div className="flex h-full min-h-[200px] items-center justify-center rounded-[14px] border border-dashed border-line-strong">
              <p className="text-sm text-muted">{t("docs_select_file")}</p>
            </div>
          )}

          {selected && !error && (
            <article aria-busy={loadingDoc} className="rounded-[14px] border border-line bg-surface">
              <div className="break-all border-b border-line px-6 py-3 font-mono text-xs text-muted">{selected}</div>
              <div className="px-6 py-5">
                {loadingDoc ? (
                  <div className="flex flex-col gap-2.5" aria-hidden="true">
                    {["40%", "92%", "78%", "85%"].map((w) => (
                      <div key={w} className="h-3 animate-pulse rounded-md bg-accent-tint" style={{ width: w }} />
                    ))}
                  </div>
                ) : (
                  <MarkdownViewer content={content ?? ""} />
                )}
              </div>
            </article>
          )}
        </section>
      </div>
    </div>
  );
}
