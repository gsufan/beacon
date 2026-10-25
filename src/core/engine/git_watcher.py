"""Incremental change detection with git.

Compares the last indexed commit with HEAD (or the working tree) and returns the added, modified,
deleted and renamed files, so the indexer only processes what changed.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import git


INDEXABLE_EXTENSIONS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".go",
    ".cs", ".cpp", ".cc", ".hpp", ".c", ".h", ".rs", ".php",
}

# Path fragments that indicate generated code (protobuf and gRPC stubs, vendored dependencies,
# node_modules, builds). Excluded when indexing because it dilutes search against the real logic.
EXCLUDED_PATH_MARKERS = [
    "_pb2.py", "_pb2_grpc.py",
    ".pb.go", "_grpc.pb.go",
    ".pb.h", ".pb.cc",
    "genproto/", "/generated/", "/gen/",
    "vendor/", "node_modules/", "/dist/", "/build/",
    ".min.js",
]


def _is_generated_or_vendor(path: str) -> bool:
    normalized = path.replace("\\", "/")
    return any(marker in normalized for marker in EXCLUDED_PATH_MARKERS)


@dataclass
class ChangeSet:
    added: List[str] = field(default_factory=list)
    modified: List[str] = field(default_factory=list)
    deleted: List[str] = field(default_factory=list)
    renamed: List[tuple] = field(default_factory=list)  # (old_path, new_path)
    # True when there is no valid base commit and the whole repository was listed: what was already in the
    # index may belong to files that no longer exist.
    full_rescan: bool = False

    def files_to_reindex(self) -> List[str]:
        return self.added + self.modified + [new for _, new in self.renamed]

    def files_to_purge(self) -> List[str]:
        return self.deleted + [old for old, _ in self.renamed]

    def is_empty(self) -> bool:
        return not (self.added or self.modified or self.deleted or self.renamed)

    def summary(self) -> str:
        return (f"+{len(self.added)} añadidos, "
                f"~{len(self.modified)} modificados, "
                f"-{len(self.deleted)} eliminados, "
                f"→{len(self.renamed)} renombrados")


def _is_indexable(path: str) -> bool:
    return Path(path).suffix.lower() in INDEXABLE_EXTENSIONS and not _is_generated_or_vendor(path)


def _classify(changes: "ChangeSet", diff_item) -> None:
    kind, old, new = diff_item.change_type, diff_item.a_path, diff_item.b_path
    if kind in ("A", "C"):
        if _is_indexable(new):
            changes.added.append(new)
    elif kind in ("M", "T"):
        if _is_indexable(new):
            changes.modified.append(new)
    elif kind == "D":
        if _is_indexable(old):
            changes.deleted.append(old)
    elif kind == "R":
        # If a rename crosses the boundary of what is indexable (e.g. .py -> .txt, or into vendor/), it is
        # treated as a deletion or an addition so no orphan chunks remain.
        old_ok, new_ok = _is_indexable(old), _is_indexable(new)
        if old_ok and new_ok:
            changes.renamed.append((old, new))
        elif old_ok:
            changes.deleted.append(old)
        elif new_ok:
            changes.added.append(new)


class GitWatcher:
    def __init__(self, repo_path: str):
        self.repo = git.Repo(repo_path)
        self.repo_path = repo_path

    def current_commit_hash(self) -> str:
        return self.repo.head.commit.hexsha

    def read_file_at(self, commit_hash: str, path: str) -> Optional[str]:
        """Content of `path` as it is in that commit (not on disk), so the index matches the recorded hash even
        with uncommitted changes. None if it is not a regular file (e.g. a symlink).
        """
        try:
            blob = self.repo.commit(commit_hash).tree / path
        except KeyError:
            return None
        if blob.type != "blob" or blob.mode == 0o120000:  # 0o120000 = symlink
            return None
        return blob.data_stream.read().decode("utf-8", errors="ignore")

    def get_changes_since(self, last_indexed_commit: Optional[str], target: Optional[str] = None) -> ChangeSet:
        """Compares the already indexed commit with `target` (HEAD by default). If last_indexed_commit is
        None (first run) the whole repository counts as "added". The caller passes the `target` captured at
        the start so the diff, the content read and the recorded hash belong to the same commit.
        """
        head = self.repo.commit(target) if target else self.repo.head.commit
        changes = ChangeSet()

        if last_indexed_commit is None:
            changes.full_rescan = True
            for item in head.tree.traverse():
                if item.type == "blob" and _is_indexable(item.path):
                    changes.added.append(item.path)
            return changes

        try:
            old_commit = self.repo.commit(last_indexed_commit)
            old_commit.tree  # with a full sha, repo.commit() does not verify the object exists
        except (git.BadName, git.BadObject, ValueError):
            # The stored commit no longer exists (rebase, rewritten history): force a full reindex.
            return self.get_changes_since(None, target=head.hexsha)

        for diff_item in old_commit.diff(head):
            _classify(changes, diff_item)
        return changes

    def get_uncommitted_changes(self) -> ChangeSet:
        """Changes in the working directory that are not committed yet."""
        changes = ChangeSet()

        for diff_item in self.repo.head.commit.diff(None):
            _classify(changes, diff_item)

        for path in self.repo.untracked_files:
            if _is_indexable(path):
                changes.added.append(path)

        return changes


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Uso: python git_watcher.py <ruta_repo> [commit_hash_anterior]")
        sys.exit(1)

    repo_path = sys.argv[1]
    last_commit = sys.argv[2] if len(sys.argv) > 2 else None

    watcher = GitWatcher(repo_path)
    print(f"HEAD actual: {watcher.current_commit_hash()}")

    changes = watcher.get_changes_since(last_commit)
    print(f"\nCambios desde {'(inicio)' if last_commit is None else last_commit[:8]}:")
    print(f"  {changes.summary()}")
    print(f"  Añadidos: {changes.added}")
    print(f"  Modificados: {changes.modified}")
    print(f"  Eliminados: {changes.deleted}")
    print(f"  Renombrados: {changes.renamed}")

    uncommitted = watcher.get_uncommitted_changes()
    if not uncommitted.is_empty():
        print(f"\nCambios sin commitear: {uncommitted.summary()}")
