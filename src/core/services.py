"""Casos de uso de Beacon, compartidos por la CLI, la API y el watcher.

Antes, cada punto de entrada implementaba su propia versión de los flujos y
se habían separado: la API aceptaba token para repos privados y la CLI no;
la CLI purgaba código generado y la API no; solo el watcher hacía `git pull`,
así que 'beacon sync' y el botón "Sincronizar" nunca traían cambios de un
repo clonado por URL. Ahora la lógica vive aquí una sola vez, y la CLI y la
API solo traducen entradas y errores (mensajes de consola o códigos HTTP).

Errores que pueden lanzar estas funciones:
  InvalidProjectIdError, InvalidRepoUrlError, InvalidRequestError  -> datos inválidos
  ProjectAlreadyExistsError -> id ya registrado
  ProjectNotFoundError      -> id no registrado
  CloneError                -> no se pudo clonar o actualizar desde el remoto
  ProjectBusyError          -> otro proceso está sincronizando el proyecto
"""

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import git

from core.config import (
    ProjectAlreadyExistsError,
    ProjectEntry,
    add_project,
    delete_project_token,
    get_project_token,
    load_config,
    remove_project,
    set_project_token,
    validate_project_id,
)
from core.engine.chroma_utils import bump_index_version, release_chroma_client
from core.engine.doc_generator import DocGenerator
from core.engine.git_watcher import _is_generated_or_vendor
from core.engine.indexer import CodebaseIndexer
from core.project_lock import ProjectBusyError, ensure_not_busy, project_lock
from core.projects import (
    DATA_ROOT,
    ProjectNotFoundError,
    build_clone_url,
    ensure_project_dirs,
    get_project,
    safe_project_dir,
    validate_repo_url,
)

ProgressCallback = Optional[Callable[[int, int, str], None]]


class InvalidRequestError(ValueError):
    """Datos de entrada que no permiten ejecutar la operación."""


class CloneError(RuntimeError):
    """No se pudo clonar el repositorio remoto ni traer sus cambios."""


# ------------------------------------------------------------ proyectos ----

def register_project(project_id: str, *, name: Optional[str] = None, repo_path: Optional[str] = None,
                     repo_url: Optional[str] = None, auth_token: Optional[str] = None,
                     auto_watch: bool = False) -> ProjectEntry:
    """Registra un repo local ya clonado (`repo_path`) o clona uno remoto
    (`repo_url`, con token opcional para repos privados)."""
    validate_project_id(project_id)
    if bool(repo_path) == bool(repo_url):
        raise InvalidRequestError("Indica la ruta de un repositorio local o la URL de uno remoto (solo una de las dos).")
    # Antes de clonar: evita descargar un repo completo para después fallar.
    if any(p.id == project_id for p in load_config().projects):
        raise ProjectAlreadyExistsError(f"El proyecto '{project_id}' ya está registrado.")

    if repo_path:
        if not Path(repo_path).exists():
            raise InvalidRequestError(f"'{repo_path}' no existe.")
        try:
            git.Repo(repo_path)
        except git.InvalidGitRepositoryError:
            raise InvalidRequestError(f"'{repo_path}' no es un repositorio git.")
        resolved_path, source_type = repo_path, "local"
    else:
        validate_repo_url(repo_url)
        resolved_path, source_type = str(DATA_ROOT / project_id / "repo"), "git"
        try:
            cloned = git.Repo.clone_from(build_clone_url(repo_url, auth_token), resolved_path)
        except git.GitCommandError as e:
            raise CloneError(f"No se pudo clonar '{repo_url}': {e}")
        if auth_token:
            # El token quedó en .git/config (remote origin): se deja la URL
            # limpia para que el secreto viva solo en credentials.yaml.
            cloned.remotes.origin.set_url(repo_url)

    entry = add_project(ProjectEntry(
        id=project_id, name=name or project_id, repo_path=resolved_path,
        source_type=source_type, repo_url=repo_url, auto_watch=auto_watch,
    ))
    ensure_project_dirs(entry.id)
    if source_type == "git" and auth_token:
        set_project_token(entry.id, auth_token)
    return entry


def unregister_project(project_id: str, *, purge_data: bool = False) -> None:
    """Quita el proyecto de config.yaml y su token; con `purge_data`, borra
    además data/<id>/ (índice, documentación y clon)."""
    trash = None
    if purge_data:
        try:
            project_dir = safe_project_dir(project_id, DATA_ROOT)
        except ValueError as e:
            raise InvalidRequestError(f"No se purgaron datos: {e}")
        if project_dir.exists():
            ensure_not_busy(project_dir)  # no borrar data/<id>/ mientras otro proceso la escribe
            release_chroma_client(str(project_dir / "chroma_db"))
            # Primero se renombra: en Windows falla si otro proceso (el
            # servidor u otra consola) tiene abierto el índice, y entonces no
            # se toca nada. Antes se desregistraba primero y el borrado fallaba
            # después, dejando el proyecto fuera de config.yaml con sus datos.
            trash = project_dir.with_name(f".{project_dir.name}.borrando")
            if trash.exists():
                shutil.rmtree(trash, ignore_errors=True)
            try:
                project_dir.rename(trash)
            except OSError:
                raise ProjectBusyError(
                    f"Los datos de '{project_id}' están en uso por otro proceso (el servidor u otra "
                    "consola). Ciérralo e inténtalo de nuevo; no se borró nada.")
    if not remove_project(project_id):
        if trash is not None:
            trash.rename(project_dir)  # no estaba registrado: se deja todo como estaba
        raise ProjectNotFoundError(f"Proyecto '{project_id}' no encontrado en config.yaml.")
    delete_project_token(project_id)
    if trash is not None:
        shutil.rmtree(trash, ignore_errors=True)


# ------------------------------------------------------ sincronización ----

@dataclass
class SyncResult:
    index: dict
    docs: Optional[dict] = None
    pulled: bool = False
    generated_purged: int = 0


def _project_entry(project_id: str) -> ProjectEntry:
    entry = next((p for p in load_config().projects if p.id == project_id), None)
    if entry is None:
        raise ProjectNotFoundError(f"Proyecto '{project_id}' no encontrado en config.yaml.")
    return entry


def pull_from_remote(entry: ProjectEntry) -> None:
    """Trae los cambios del remoto de un proyecto clonado por URL. Si tiene
    token (repo privado), lo usa solo durante el pull y restaura la URL
    limpia después, para no dejar el secreto en .git/config."""
    repo = git.Repo(entry.repo_path)
    token = get_project_token(entry.id)
    try:
        if not token:
            repo.remotes.origin.pull()
            return
        original_url = entry.repo_url or next(repo.remotes.origin.urls)
        try:
            repo.remotes.origin.set_url(build_clone_url(original_url, token))
            repo.remotes.origin.pull()
        finally:
            repo.remotes.origin.set_url(original_url)
    except git.GitCommandError as e:
        raise CloneError(f"No se pudieron traer los cambios del remoto de '{entry.id}': {e}")


def _purge_generated(indexer: CodebaseIndexer) -> int:
    """Quita chunks de código generado/vendor que hayan quedado de índices
    anteriores a ese filtro (el indexado actual ya no los agrega)."""
    data = indexer.collection.get(include=["metadatas"])
    to_delete = [cid for cid, m in zip(data["ids"], data["metadatas"])
                 if _is_generated_or_vendor(m.get("file_path", ""))]
    if to_delete:
        indexer.collection.delete(ids=to_delete)
        bump_index_version(Path(indexer.project.chroma_dir).parent)
    return len(to_delete)


def sync_project(project_id: str, *, docs: bool = True, full: bool = False, include_uncommitted: bool = False,
                 pull: bool = True, on_index_progress: ProgressCallback = None,
                 on_docs_progress: ProgressCallback = None) -> SyncResult:
    """Pone al día un proyecto bajo el lock entre procesos: trae los cambios
    del remoto (solo proyectos clonados por URL), actualiza el índice y,
    con `docs`, la documentación. El `git pull` ocurre dentro del lock, así
    que nunca cambia archivos a mitad del indexado de otra consola."""
    project = get_project(project_id)
    entry = _project_entry(project_id)
    ai = load_config().ai_provider
    with project_lock(Path(project.chroma_dir).parent):
        pulled = False
        if pull and entry.source_type == "git" and not include_uncommitted:
            pull_from_remote(entry)
            pulled = True
        indexer = CodebaseIndexer(project, ai)
        index_result = indexer.sync(include_uncommitted=include_uncommitted, on_progress=on_index_progress, full=full)
        purged = _purge_generated(indexer) if index_result.get("status") != "sin_cambios" else 0
        docs_result = None
        if docs and not include_uncommitted:  # la documentación siempre refleja un commit
            docs_result = DocGenerator(project, ai).sync(on_progress=on_docs_progress, full=full)
    return SyncResult(index=index_result, docs=docs_result, pulled=pulled, generated_purged=purged)


def generate_docs(project_id: str, *, full: bool = False, on_progress: ProgressCallback = None) -> dict:
    """Actualiza solo la documentación (requiere el índice al día)."""
    project = get_project(project_id)
    ai = load_config().ai_provider
    with project_lock(Path(project.chroma_dir).parent):
        return DocGenerator(project, ai).sync(on_progress=on_progress, full=full)
