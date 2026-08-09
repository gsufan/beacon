"""
Registro de proyectos: cada proyecto tiene su propio repo, su propio índice
ChromaDB y su propia carpeta de documentación generada — completamente
aislados entre sí. Así una empresa puede registrar varios repos y que
distintos devs trabajen con cada uno sin que se pisen los índices.
"""

from dataclasses import dataclass
from pathlib import Path

from core.config import load_config

DATA_ROOT = Path(__file__).resolve().parents[2] / "data"


@dataclass
class ProjectContext:
    id: str
    name: str
    repo_path: str
    chroma_dir: str
    docs_dir: str


class ProjectNotFoundError(Exception):
    pass


def list_projects() -> list[ProjectContext]:
    cfg = load_config()
    return [_to_context(p) for p in cfg.projects]


def get_project(project_id: str) -> ProjectContext:
    cfg = load_config()
    for p in cfg.projects:
        if p.id == project_id:
            return _to_context(p)
    known = ", ".join(p.id for p in cfg.projects) or "(ninguno registrado)"
    raise ProjectNotFoundError(f"Proyecto '{project_id}' no encontrado. Proyectos conocidos: {known}")


def _to_context(entry) -> ProjectContext:
    project_dir = DATA_ROOT / entry.id
    return ProjectContext(
        id=entry.id,
        name=entry.name,
        repo_path=entry.repo_path,
        chroma_dir=str(project_dir / "chroma_db"),
        docs_dir=str(project_dir / "docs"),
    )
