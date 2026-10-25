"""Disk usage per project and selective cleanup.

Each project keeps up to three things in data/<id>/, which can be deleted separately: the repository
clone (only if Beacon cloned it from a URL; a user's local folder is never touched), the vector index
and the generated documentation. The project stays registered: what was deleted is recreated on the
next `sync`.
"""

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import List

from core.config import ProjectEntry, load_config
from core.engine.chroma_utils import bump_index_version, release_chroma_client
from core.project_lock import ensure_not_busy
from core.projects import DATA_ROOT, ProjectNotFoundError, ensure_project_dirs, safe_project_dir


@dataclass
class ProjectUsage:
    id: str
    name: str
    source_type: str
    clone_bytes: int
    index_bytes: int
    docs_bytes: int
    clone_path: str  # folder of the clone managed by Beacon ("" if the repo is a user's local folder)

    @property
    def total_bytes(self) -> int:
        return self.clone_bytes + self.index_bytes + self.docs_bytes


def dir_size(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for p in path.rglob("*"):
        try:
            if p.is_file() and not p.is_symlink():
                total += p.stat().st_size
        except OSError:
            continue
    return total


def _managed_clone(entry: ProjectEntry, data_root: Path) -> Path | None:
    """The clone belongs to Beacon only if it created it: a URL project, inside data/<id>/."""
    if entry.source_type != "git":
        return None
    path = Path(entry.repo_path).resolve()
    return path if path.is_relative_to((data_root / entry.id).resolve()) else None


def project_usage(entry: ProjectEntry, data_root: Path | None = None) -> ProjectUsage:
    data_root = data_root or DATA_ROOT
    project_dir = data_root / entry.id
    clone = _managed_clone(entry, data_root)
    return ProjectUsage(
        id=entry.id, name=entry.name, source_type=entry.source_type,
        clone_bytes=dir_size(clone) if clone else 0, index_bytes=dir_size(project_dir / "chroma_db"),
        docs_bytes=dir_size(project_dir / "docs"), clone_path=str(clone) if clone else "",
    )


def list_usage(data_root: Path | None = None) -> List[ProjectUsage]:
    return [project_usage(p, data_root) for p in load_config().projects]


def _remove_dir(path: Path):
    """On Windows, deleting fails if another process has it open: report it instead of leaving things half done."""
    shutil.rmtree(path)


def clean_project(project_id: str, *, clone: bool = False, index: bool = False, docs: bool = False) -> int:
    entry = next((p for p in load_config().projects if p.id == project_id), None)
    if entry is None:
        raise ProjectNotFoundError(f"Proyecto '{project_id}' no encontrado en config.yaml.")
    project_dir = safe_project_dir(project_id, DATA_ROOT)
    usage = project_usage(entry)
    freed = 0
    if project_dir.exists() and (clone or index or docs):
        ensure_not_busy(project_dir)  # do not delete while another process (sync, server) writes
    if index and (project_dir / "chroma_db").exists():
        release_chroma_client(str(project_dir / "chroma_db"))
        _remove_dir(project_dir / "chroma_db")
        freed += usage.index_bytes
        bump_index_version(project_dir)  # a running server must not keep using the deleted index
    if docs and (project_dir / "docs").exists():
        _remove_dir(project_dir / "docs")
        freed += usage.docs_bytes
    managed = _managed_clone(entry, DATA_ROOT)
    if clone and managed is not None and managed.exists():
        _remove_dir(managed)
        freed += usage.clone_bytes
    ensure_project_dirs(project_id)  # the empty folders the rest of the system expects
    return freed


def human_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} B"
