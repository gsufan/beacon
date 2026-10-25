"""Beacon use cases, shared by the CLI, the API and the watcher.

The logic lives here once; the CLI and the API only translate inputs and errors (console messages
or HTTP codes). Errors raised by these functions: InvalidProjectIdError, InvalidRepoUrlError and
InvalidRequestError (invalid data), ProjectAlreadyExistsError, ProjectNotFoundError, CloneError
(clone or update from the remote failed) and ProjectBusyError (another process is syncing).
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
    pass


class CloneError(RuntimeError):
    pass



def register_project(project_id: str, *, name: Optional[str] = None, repo_path: Optional[str] = None,
                     repo_url: Optional[str] = None, auth_token: Optional[str] = None,
                     auto_watch: bool = False) -> ProjectEntry:
    """Registers an already cloned local repo (`repo_path`) or clones a remote one (`repo_url`, with an optional token for private repos)."""
    validate_project_id(project_id)
    if bool(repo_path) == bool(repo_url):
        raise InvalidRequestError("Indica la ruta de un repositorio local o la URL de uno remoto (solo una de las dos).")
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
            # The token was left in .git/config (remote origin): restore the clean URL so the secret lives only
            # in credentials.yaml.
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
    """Removes the project from config.yaml and its token; with `purge_data`, also deletes data/<id>/ (index, documentation and clone)."""
    trash = None
    if purge_data:
        try:
            project_dir = safe_project_dir(project_id, DATA_ROOT)
        except ValueError as e:
            raise InvalidRequestError(f"No se purgaron datos: {e}")
        if project_dir.exists():
            ensure_not_busy(project_dir)  # do not delete data/<id>/ while another process writes it
            release_chroma_client(str(project_dir / "chroma_db"))
            # Rename first: on Windows it fails if another process has the index open, and then nothing is touched,
            # so the project is never left out of config.yaml with its data still on disk.
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
            trash.rename(project_dir)  # it was not registered: leave everything as it was
        raise ProjectNotFoundError(f"Proyecto '{project_id}' no encontrado en config.yaml.")
    delete_project_token(project_id)
    if trash is not None:
        shutil.rmtree(trash, ignore_errors=True)



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


def ensure_clone(entry: ProjectEntry) -> bool:
    """Clones again a URL project whose clone was deleted to free space. True if it had to clone."""
    if entry.source_type != "git" or not entry.repo_url or Path(entry.repo_path).exists():
        return False
    token = get_project_token(entry.id)
    try:
        cloned = git.Repo.clone_from(build_clone_url(entry.repo_url, token), entry.repo_path)
    except git.GitCommandError as e:
        raise CloneError(f"No se pudo volver a clonar '{entry.repo_url}': {e}")
    if token:
        cloned.remotes.origin.set_url(entry.repo_url)
    return True


def pull_from_remote(entry: ProjectEntry) -> None:
    """Pulls remote changes of a URL-cloned project. A token (private repo) is only used during the pull and
    the clean URL is restored afterwards, so the secret is not left in .git/config.
    """
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
    """Removes generated/vendor chunks left by indexes created before that filter (current indexing no longer adds them)."""
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
    """Brings a project up to date under the cross-process lock: pulls remote changes (URL projects only),
    updates the index and, with `docs`, the documentation. The `git pull` happens inside the lock so it never
    changes files in the middle of another console's indexing.
    """
    project = get_project(project_id)
    entry = _project_entry(project_id)
    ai = load_config().ai_provider
    with project_lock(Path(project.chroma_dir).parent):
        pulled = ensure_clone(entry)  # a fresh clone is already up to date
        if pull and not pulled and entry.source_type == "git" and not include_uncommitted:
            pull_from_remote(entry)
            pulled = True
        indexer = CodebaseIndexer(project, ai)
        index_result = indexer.sync(include_uncommitted=include_uncommitted, on_progress=on_index_progress, full=full)
        purged = _purge_generated(indexer) if index_result.get("status") != "sin_cambios" else 0
        docs_result = None
        if docs and not include_uncommitted:  # documentation always reflects a commit
            docs_result = DocGenerator(project, ai).sync(on_progress=on_docs_progress, full=full)
    return SyncResult(index=index_result, docs=docs_result, pulled=pulled, generated_purged=purged)


def generate_docs(project_id: str, *, full: bool = False, on_progress: ProgressCallback = None) -> dict:
    project = get_project(project_id)
    ai = load_config().ai_provider
    with project_lock(Path(project.chroma_dir).parent):
        ensure_clone(_project_entry(project_id))
        return DocGenerator(project, ai).sync(on_progress=on_progress, full=full)
