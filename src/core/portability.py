"""Export and import of an indexed project as a portable .zip (index, documentation and a manifest).

Errors raised: ProjectNotFoundError, NoIndexedDataError, InvalidArchiveError, ProjectAlreadyExistsError,
InvalidProjectIdError and services.InvalidRequestError (the target data folder already exists).
"""

import json
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from core.config import ProjectAlreadyExistsError, ProjectEntry, add_project, load_config, validate_project_id
from core.engine.chroma_utils import bump_index_version
from core.project_lock import LOCK_FILENAME
from core.projects import DATA_ROOT, ProjectNotFoundError
from core.services import InvalidRequestError

MANIFEST = "manifest.json"
_DATA_PREFIX = "data/"


class NoIndexedDataError(InvalidRequestError):
    pass


class InvalidArchiveError(InvalidRequestError):
    pass


@dataclass
class ImportResult:
    entry: ProjectEntry
    repo_path_exists: bool  # without the repository, queries work but incremental sync and docs do not


def _data_root(data_root: Optional[Path]) -> Path:
    return data_root or DATA_ROOT


def export_project(project_id: str, output_path: Path, *, data_root: Optional[Path] = None) -> Path:
    entry = load_config().find_project(project_id)
    if entry is None:
        raise ProjectNotFoundError(f"Proyecto '{project_id}' no encontrado en config.yaml.")
    project_dir = _data_root(data_root) / project_id
    if not project_dir.exists():
        raise NoIndexedDataError(f"No hay datos indexados para '{project_id}' todavía: corre 'beacon sync {project_id}' primero.")
    manifest = {"id": entry.id, "name": entry.name, "repo_path": entry.repo_path,
                "source_type": entry.source_type, "repo_url": entry.repo_url}
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(MANIFEST, json.dumps(manifest, indent=2, ensure_ascii=False))
        for file in project_dir.rglob("*"):
            if file.is_file() and file.name != LOCK_FILENAME:
                zf.write(file, arcname=str(Path("data") / file.relative_to(project_dir)))
    return output_path


def _extract_data(zf: zipfile.ZipFile, target_dir: Path):
    """Only entries under data/ that stay inside target_dir (a tampered .zip may carry '../' paths)."""
    target_dir.mkdir(parents=True, exist_ok=True)
    for member in zf.namelist():
        if not member.startswith(_DATA_PREFIX):
            continue
        dest = (target_dir / Path(member).relative_to("data")).resolve()
        if not dest.is_relative_to(target_dir):
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(member) as src, open(dest, "wb") as out:
            out.write(src.read())


def import_project(zip_path: Path, *, project_id: Optional[str] = None, repo_path: Optional[str] = None,
                   data_root: Optional[Path] = None) -> ImportResult:
    """Registers a project from a .zip made by export_project. Everything is checked before touching the disk."""
    if not zip_path.exists():
        raise InvalidArchiveError(f"No existe el archivo '{zip_path}'.")
    with zipfile.ZipFile(zip_path) as zf:
        try:
            manifest = json.loads(zf.read(MANIFEST))
        except KeyError:
            raise InvalidArchiveError("El .zip no tiene manifest.json: ¿fue generado con 'beacon export'?") from None
        target_id = project_id or manifest["id"]
        validate_project_id(target_id)  # it comes from the manifest
        if load_config().find_project(target_id):
            raise ProjectAlreadyExistsError(f"El proyecto '{target_id}' ya está registrado: usa --id para elegir otro nombre.")
        target_dir = (_data_root(data_root) / target_id).resolve()
        if target_dir.exists():
            raise InvalidRequestError(f"Ya existe data/{target_id}/: usa --id para elegir otro nombre, o borra esa carpeta primero.")
        _extract_data(zf, target_dir)
    bump_index_version(target_dir)  # a running server must not use previous data of this id
    final_repo_path = repo_path or manifest["repo_path"]
    entry = add_project(ProjectEntry(
        id=target_id, name=manifest["name"], repo_path=final_repo_path,
        source_type=manifest.get("source_type", "local"), repo_url=manifest.get("repo_url"),
    ))
    return ImportResult(entry=entry, repo_path_exists=Path(final_repo_path).exists())
