"""API REST multi-proyecto. La UI (frontend/dist) se sirve como estáticos, ver el final del archivo."""

import threading
import time
from functools import lru_cache
from pathlib import Path
from typing import Optional

import git
import ollama
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from core.config import (
    ProjectAlreadyExistsError,
    ProjectEntry,
    add_project,
    delete_project_token,
    get_project_token,
    load_config,
    remove_project,
    set_auto_watch,
    set_project_token,
    update_ai_provider,
)
from core.projects import DATA_ROOT, ensure_project_dirs, get_project, list_projects, ProjectNotFoundError
from core.engine.doc_generator import DocGenerator
from core.engine.indexer import CodebaseIndexer
from core.engine.rag_engine import RAGEngine

app = FastAPI(title="Beacon API", version="0.3.0", docs_url="/api/docs", redoc_url="/api/redoc")
# Beacon se sirve same-origin en producción (la UI compilada vive en el mismo
# host:puerto que la API); el único caso legítimo de origen cruzado es el dev
# server de Vite. No usar "*" — sin auth en la API (ver README/ARQUITECTURA),
# un CORS abierto ampliaría la superficie de ataque sin necesidad real.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_sync_status: dict = {}
_sync_lock = threading.Lock()

AUTO_WATCH_INTERVAL_SECONDS = 300


@lru_cache(maxsize=None)
def _get_engine(project_id: str) -> RAGEngine:
    """Una instancia de RAGEngine por proyecto, reutilizada entre requests."""
    project = get_project(project_id)
    cfg = load_config()
    return RAGEngine(project, cfg.ai_provider)


class QueryRequest(BaseModel):
    question: str = Field(..., min_length=3)
    top_k: int = Field(default=5, ge=1, le=20)
    language: str = Field(default="es", pattern="^(es|en)$")


class SourceItem(BaseModel):
    file_path: str
    chunk_type: str
    name: str
    start_line: int
    end_line: int
    distance: float
    expanded: bool = False


class QueryResponse(BaseModel):
    answer: str
    sources: list[SourceItem]


class AIProviderUpdate(BaseModel):
    provider: str
    ollama_host: str
    embedding_model: str
    llm_model: str


class ProjectCreate(BaseModel):
    id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    source_type: str = Field(default="local", pattern="^(local|git)$")
    repo_path: Optional[str] = None  # requerido si source_type == "local"
    repo_url: Optional[str] = None  # requerido si source_type == "git"
    auth_token: Optional[str] = None  # opcional, solo para repos git privados


class AutoWatchUpdate(BaseModel):
    enabled: bool


@app.get("/projects")
def get_projects():
    return [{"id": p.id, "name": p.name} for p in list_projects()]


@app.get("/projects/{project_id}/health")
def project_health(project_id: str):
    try:
        engine = _get_engine(project_id)
    except ProjectNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"status": "ok", "total_chunks_indexados": engine.collection.count()}


@app.post("/projects/{project_id}/query", response_model=QueryResponse)
def query(project_id: str, req: QueryRequest):
    try:
        engine = _get_engine(project_id)
    except ProjectNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    if engine.collection.count() == 0:
        raise HTTPException(status_code=409, detail=f"El índice de '{project_id}' está vacío. Corre 'deuda-tecnica sync {project_id}' primero.")
    try:
        result = engine.ask(req.question, top_k=req.top_k, language=req.language)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error en el motor RAG: {e}")
    return result.to_dict()


def _require_engine(project_id: str) -> RAGEngine:
    try:
        return _get_engine(project_id)
    except ProjectNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


def _require_project(project_id: str):
    try:
        return get_project(project_id)
    except ProjectNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/projects/{project_id}/docs/tree")
def docs_tree(project_id: str):
    project = _require_project(project_id)
    docs_dir = Path(project.docs_dir)
    if not docs_dir.exists():
        return {"files": []}
    files = sorted(
        p.relative_to(docs_dir).as_posix()[: -len(".md")]
        for p in docs_dir.rglob("*.md")
    )
    return {"files": files}


@app.get("/projects/{project_id}/docs")
def docs(project_id: str, file_path: str):
    project = _require_project(project_id)
    docs_dir = Path(project.docs_dir).resolve()
    doc_path = (docs_dir / f"{file_path}.md").resolve()
    if not doc_path.is_relative_to(docs_dir):
        raise HTTPException(status_code=403, detail="Ruta fuera del directorio de documentación del proyecto.")
    if not doc_path.exists():
        raise HTTPException(status_code=404, detail=f"No hay documentación generada para '{file_path}'.")
    return {"file_path": file_path, "content_markdown": doc_path.read_text(encoding="utf-8")}


@app.get("/config")
def get_config():
    cfg = load_config()
    return {
        "ai_provider": cfg.ai_provider.__dict__,
        "projects": [p.__dict__ for p in cfg.projects],
    }


@app.put("/config/ai-provider")
def put_ai_provider(body: AIProviderUpdate):
    updated = update_ai_provider(
        provider=body.provider, ollama_host=body.ollama_host,
        embedding_model=body.embedding_model, llm_model=body.llm_model,
    )
    _get_engine.cache_clear()
    return updated.__dict__


ALLOWED_GIT_URL_PREFIXES = ("http://", "https://", "git@", "ssh://")


def _validate_repo_url(repo_url: str):
    """Rechaza esquemas que no sean http(s)/ssh — en particular `ext::`, que
    git soporta para invocar un comando de transporte arbitrario y es un
    vector de inyección de comandos conocido si se deja pasar sin validar.
    """
    if not repo_url.startswith(ALLOWED_GIT_URL_PREFIXES):
        raise HTTPException(
            status_code=400,
            detail="'repo_url' debe ser http(s)://, ssh:// o git@... — otros esquemas no están permitidos.",
        )


def _clone_url(repo_url: str, auth_token: Optional[str]) -> str:
    """Inserta el token en la URL solo para el clonado — nunca se persiste con la URL."""
    if not auth_token or "://" not in repo_url:
        return repo_url
    scheme, rest = repo_url.split("://", 1)
    return f"{scheme}://{auth_token}@{rest}"


@app.get("/system/providers")
def system_providers():
    """Proveedores de IA soportados. Hoy solo Ollama está implementado."""
    return {"providers": [{"id": "ollama", "name": "Ollama (local)"}]}


@app.get("/system/available-models")
def system_available_models(ollama_host: Optional[str] = None):
    """Modelos realmente instalados en el host de Ollama configurado (o el pasado por query)."""
    host = ollama_host or load_config().ai_provider.ollama_host
    try:
        client = ollama.Client(host=host)
        models = [m["model"] for m in client.list().get("models", [])]
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"No se pudo conectar a Ollama en '{host}': {e}")
    return {"models": models}


BROWSE_ROOT = Path.home().resolve()


@app.get("/system/browse-dirs")
def system_browse_dirs(path: Optional[str] = None):
    """Explora el filesystem DEL SERVIDOR donde corre este backend (no el del navegador).

    Beacon siempre lee repos desde el disco donde se ejecuta `beacon serve`; un
    selector de archivos del navegador vería el filesystem equivocado si el
    backend corre en otra máquina. Por eso el explorador vive acá.

    Acotado a BROWSE_ROOT (home del usuario): sin esto, cualquiera con acceso
    a la API podría enumerar cualquier carpeta del disco (la API no tiene
    autenticación — ver limitaciones conocidas en el README).
    """
    base = (Path(path) if path else BROWSE_ROOT).resolve()
    if not base.is_relative_to(BROWSE_ROOT):
        raise HTTPException(status_code=403, detail=f"Fuera del directorio permitido ({BROWSE_ROOT}).")
    if not base.exists() or not base.is_dir():
        raise HTTPException(status_code=400, detail=f"'{base}' no es una carpeta válida en este servidor.")
    try:
        entries = sorted(
            p.name for p in base.iterdir() if p.is_dir() and not p.name.startswith(".")
        )
    except PermissionError:
        raise HTTPException(status_code=403, detail=f"Sin permisos para leer '{base}'.")
    return {
        "path": str(base),
        "parent": str(base.parent) if base != BROWSE_ROOT else None,
        "directories": entries,
    }


@app.post("/projects", status_code=201)
def create_project(body: ProjectCreate):
    if body.source_type == "local":
        if not body.repo_path:
            raise HTTPException(status_code=400, detail="'repo_path' es requerido para source_type='local'.")
        if not Path(body.repo_path).exists():
            raise HTTPException(status_code=400, detail=f"'{body.repo_path}' no existe.")
        try:
            git.Repo(body.repo_path)
        except git.InvalidGitRepositoryError:
            raise HTTPException(status_code=400, detail=f"'{body.repo_path}' no es un repositorio git.")
        resolved_path = body.repo_path
    else:
        if not body.repo_url:
            raise HTTPException(status_code=400, detail="'repo_url' es requerido para source_type='git'.")
        _validate_repo_url(body.repo_url)
        resolved_path = str(DATA_ROOT / body.id / "repo")
        try:
            cloned = git.Repo.clone_from(_clone_url(body.repo_url, body.auth_token), resolved_path)
            if body.auth_token:
                # La URL con el token quedó guardada en .git/config del clon
                # (remote "origin"); la reemplazamos por la URL limpia para
                # no persistir el secreto ahí también, además de en
                # credentials.yaml.
                cloned.remotes.origin.set_url(body.repo_url)
        except git.GitCommandError as e:
            raise HTTPException(status_code=400, detail=f"No se pudo clonar '{body.repo_url}': {e}")

    try:
        entry = add_project(ProjectEntry(
            id=body.id, name=body.name, repo_path=resolved_path,
            source_type=body.source_type, repo_url=body.repo_url,
        ))
    except ProjectAlreadyExistsError as e:
        raise HTTPException(status_code=409, detail=str(e))
    ensure_project_dirs(entry.id)
    if body.source_type == "git" and body.auth_token:
        set_project_token(entry.id, body.auth_token)
    return entry.__dict__


@app.put("/projects/{project_id}/auto-watch")
def put_auto_watch(project_id: str, body: AutoWatchUpdate):
    if not set_auto_watch(project_id, body.enabled):
        raise HTTPException(status_code=404, detail=f"Proyecto '{project_id}' no encontrado.")
    return {"id": project_id, "auto_watch": body.enabled}


@app.delete("/projects/{project_id}")
def delete_project(project_id: str, purge_data: bool = False):
    if not remove_project(project_id):
        raise HTTPException(status_code=404, detail=f"Proyecto '{project_id}' no encontrado.")
    _get_engine.cache_clear()
    delete_project_token(project_id)
    if purge_data:
        import shutil
        project_dir = DATA_ROOT / project_id
        if project_dir.exists():
            shutil.rmtree(project_dir)
    return {"status": "eliminado", "purge_data": purge_data}


def _run_sync(project_id: str):
    with _sync_lock:
        _sync_status[project_id] = {"status": "running", "detail": None}
    try:
        project = get_project(project_id)
        cfg = load_config()
        indexer = CodebaseIndexer(project, cfg.ai_provider)
        index_result = indexer.sync()
        doc_gen = DocGenerator(project, cfg.ai_provider)
        doc_result = doc_gen.sync()
        _get_engine.cache_clear()
        with _sync_lock:
            _sync_status[project_id] = {
                "status": "done", "detail": {"index": index_result, "docs": doc_result},
            }
    except Exception as e:
        with _sync_lock:
            _sync_status[project_id] = {"status": "error", "detail": str(e)}


@app.post("/projects/{project_id}/sync", status_code=202)
def trigger_sync(project_id: str, background_tasks: BackgroundTasks):
    _require_project(project_id)  # 404 si no existe
    with _sync_lock:
        if _sync_status.get(project_id, {}).get("status") == "running":
            raise HTTPException(status_code=409, detail="Ya hay un sync en curso para este proyecto.")
    background_tasks.add_task(_run_sync, project_id)
    return {"status": "started"}


@app.get("/projects/{project_id}/sync-status")
def sync_status(project_id: str):
    with _sync_lock:
        return _sync_status.get(project_id, {"status": "idle", "detail": None})


def _auto_watch_loop():
    """Corre en background: cada AUTO_WATCH_INTERVAL_SECONDS, sincroniza los
    proyectos con auto_watch=True. sync() ya es idempotente (no hace nada si
    no hay commits nuevos), así que llamarlo periódicamente es seguro.
    """
    while True:
        time.sleep(AUTO_WATCH_INTERVAL_SECONDS)
        try:
            cfg = load_config()
        except Exception:
            continue
        for entry in cfg.projects:
            if not entry.auto_watch:
                continue
            with _sync_lock:
                if _sync_status.get(entry.id, {}).get("status") == "running":
                    continue
            try:
                if entry.source_type == "git":
                    _pull_git_project(entry)
                _run_sync(entry.id)
            except Exception:
                continue


def _pull_git_project(entry: ProjectEntry):
    """Actualiza un proyecto clonado antes de sincronizarlo. Si tiene token
    guardado (repo privado), lo reinyecta en la URL solo para el pull y
    restaura la URL limpia después — igual que en el clonado inicial, para
    no dejar el secreto persistido en .git/config.
    """
    repo = git.Repo(entry.repo_path)
    token = get_project_token(entry.id)
    if not token:
        repo.remotes.origin.pull()
        return
    original_url = entry.repo_url or next(repo.remotes.origin.urls)
    try:
        repo.remotes.origin.set_url(_clone_url(original_url, token))
        repo.remotes.origin.pull()
    finally:
        repo.remotes.origin.set_url(original_url)


_auto_watch_thread = threading.Thread(target=_auto_watch_loop, daemon=True)
_auto_watch_thread.start()


# --- Frontend (React/Vite compilado) ---
# SPA con client-side routing: los assets se sirven directo desde dist/,
# y cualquier otra ruta que no matchee una API ni un archivo real cae a
# index.html (para que /docs, /settings, etc. funcionen al recargar).
_FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if _FRONTEND_DIST.exists():
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    app.mount("/assets", StaticFiles(directory=str(_FRONTEND_DIST / "assets")), name="frontend-assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa_fallback(full_path: str):
        candidate = _FRONTEND_DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_FRONTEND_DIST / "index.html")
