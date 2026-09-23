"""
Registro de proyectos: cada proyecto tiene su propio repo, su propio índice
ChromaDB y su propia carpeta de documentación generada — completamente
aislados entre sí. Así una empresa puede registrar varios repos y que
distintos devs trabajen con cada uno sin que se pisen los índices.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from core.config import load_config

DATA_ROOT = Path(__file__).resolve().parents[2] / "data"

# Compartido entre api.py y cli.py para que la lista de esquemas permitidos
# viva en un solo lugar (es una validación de seguridad, no una conveniencia).
ALLOWED_GIT_URL_PREFIXES = ("http://", "https://", "git@", "ssh://")


class InvalidRepoUrlError(ValueError):
    pass


def validate_repo_url(repo_url: str):
    """Rechaza esquemas que no sean http(s)/ssh/git@ — en particular `ext::`,
    que git soporta para invocar un comando de transporte arbitrario y es un
    vector de inyección de comandos conocido si se deja pasar sin validar."""
    if not repo_url.startswith(ALLOWED_GIT_URL_PREFIXES):
        raise InvalidRepoUrlError(
            "'repo_url' debe ser http(s)://, ssh:// o git@... — otros esquemas no están permitidos."
        )


def build_clone_url(repo_url: str, auth_token: Optional[str]) -> str:
    """Inserta el token en la URL solo para el clonado — nunca se persiste con la URL."""
    if not auth_token or "://" not in repo_url:
        return repo_url
    scheme, rest = repo_url.split("://", 1)
    return f"{scheme}://{auth_token}@{rest}"


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


def safe_project_dir(project_id: str, data_root: Optional[Path] = None) -> Path:
    """data/<id>/ resuelto, garantizando que quede DENTRO de data_root.
    Defensa en profundidad para operaciones destructivas (purge) por si
    config.yaml trae un id editado a mano que no pasó por validate_project_id."""
    root = (data_root or DATA_ROOT).resolve()
    target = (root / project_id).resolve()
    if target == root or not target.is_relative_to(root):
        raise ValueError(f"El id '{project_id}' resuelve fuera de {root}.")
    return target


def ensure_project_dirs(project_id: str):
    project_dir = DATA_ROOT / project_id
    (project_dir / "chroma_db").mkdir(parents=True, exist_ok=True)
    (project_dir / "docs").mkdir(parents=True, exist_ok=True)


def _to_context(entry) -> ProjectContext:
    project_dir = DATA_ROOT / entry.id
    return ProjectContext(
        id=entry.id,
        name=entry.name,
        repo_path=entry.repo_path,
        chroma_dir=str(project_dir / "chroma_db"),
        docs_dir=str(project_dir / "docs"),
    )
