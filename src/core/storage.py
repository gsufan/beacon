"""Espacio en disco por proyecto y limpieza selectiva.

Cada proyecto guarda en data/<id>/ hasta tres cosas, que se pueden borrar por separado:
  - el clon del repositorio (solo si Beacon lo clonó por URL; una carpeta local del usuario nunca se toca),
  - el índice vectorial (se reconstruye con `beacon sync`),
  - la documentación generada (se reconstruye con `beacon docs`).
El proyecto sigue registrado: lo borrado se vuelve a crear en el siguiente `sync`."""

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
    clone_path: str  # carpeta del clon administrado por Beacon ("" si el repo es una carpeta local del usuario)

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
    """El clon solo es de Beacon si lo creó él: proyecto por URL y dentro de data/<id>/."""
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
    """En Windows, borrar falla si otro proceso la tiene abierta: se avisa en vez de dejar algo a medias."""
    shutil.rmtree(path)


def clean_project(project_id: str, *, clone: bool = False, index: bool = False, docs: bool = False) -> int:
    """Borra lo pedido y devuelve los bytes liberados. El proyecto sigue registrado."""
    entry = next((p for p in load_config().projects if p.id == project_id), None)
    if entry is None:
        raise ProjectNotFoundError(f"Proyecto '{project_id}' no encontrado en config.yaml.")
    project_dir = safe_project_dir(project_id, DATA_ROOT)
    usage = project_usage(entry)
    freed = 0
    if project_dir.exists() and (clone or index or docs):
        ensure_not_busy(project_dir)  # no borrar mientras otro proceso (sync, servidor) escribe
    if index and (project_dir / "chroma_db").exists():
        release_chroma_client(str(project_dir / "chroma_db"))
        _remove_dir(project_dir / "chroma_db")
        freed += usage.index_bytes
        bump_index_version(project_dir)  # un servidor ya levantado no debe seguir usando el índice borrado
    if docs and (project_dir / "docs").exists():
        _remove_dir(project_dir / "docs")
        freed += usage.docs_bytes
    managed = _managed_clone(entry, DATA_ROOT)
    if clone and managed is not None and managed.exists():
        _remove_dir(managed)
        freed += usage.clone_bytes
    ensure_project_dirs(project_id)  # las carpetas vacías que el resto del sistema espera
    return freed


def human_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} B"
