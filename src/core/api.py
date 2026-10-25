"""Beacon REST API. It also serves the compiled web interface (frontend/dist)."""

import logging
import os
from contextlib import asynccontextmanager
import threading
import time
from pathlib import Path
from typing import Optional

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

logger = logging.getLogger("beacon.api")

from core.config import (
    InvalidProjectIdError,
    ProjectAlreadyExistsError,
    load_config,
    set_auto_watch,
    resolve_ai_config,
    update_ai_provider,
)
from core.projects import (
    InvalidRepoUrlError,
    get_project,
    list_projects,
    ProjectNotFoundError,
)
from core.project_lock import ProjectBusyError
from core.engine.backends import (
    BackendAuthError, BackendUnavailableError, BudgetExceededError, CloudNotConfiguredError, available_providers, create_backend,
)
from core.engine.chroma_utils import read_index_version
from core.engine.indexer import IndexModelMismatchError
from core.engine.rag_engine import RAGEngine
from core import services

_shutdown_hooks: list = []


@asynccontextmanager
async def _lifespan(_app):
    yield
    for hook in _shutdown_hooks:  # on shutdown: stop whatever is in progress (e.g. a model answer)
        hook()


app = FastAPI(title="Beacon API", version="0.3.0", docs_url="/api/docs", redoc_url="/api/redoc", lifespan=_lifespan)
# The compiled interface is served from the same origin as the API; the only legitimate cross-origin
# is the Vite dev server. "*" is not used (see docs/en/security.md).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Optional API key: with BEACON_API_KEY set, data and configuration endpoints require the X-API-Key
# header (not the interface or /healthz). Without it, the API is open.
API_KEY = os.environ.get("BEACON_API_KEY")
_PROTECTED_PREFIXES = ("/projects", "/config", "/system")


@app.middleware("http")
async def api_key_middleware(request: Request, call_next):
    if API_KEY and request.url.path.startswith(_PROTECTED_PREFIXES):
        if request.headers.get("x-api-key") != API_KEY:
            return JSONResponse({"detail": "Falta o es inválido el header X-API-Key."}, status_code=401)
    return await call_next(request)


@app.get("/healthz", include_in_schema=False)
def healthz():
    """No authentication and no external dependencies, for Docker and orchestrator healthchecks."""
    return {"status": "ok"}


_sync_status: dict = {}
_sync_lock = threading.Lock()

AUTO_WATCH_INTERVAL_SECONDS = 300


_engines: dict = {}  # project_id -> (index version, RAGEngine)
_engines_lock = threading.Lock()


def _get_engine(project_id: str) -> RAGEngine:
    """One RAGEngine per project, reused across requests while the index does not change. If it changed (a
    sync in this process or in another, e.g. `beacon sync` in a console), a new one is created that sees
    the updated index (see chroma_utils).
    """
    project = get_project(project_id)
    version = read_index_version(Path(project.chroma_dir).parent)
    with _engines_lock:
        cached = _engines.get(project_id)
        if cached and cached[0] == version:
            return cached[1]
    engine = RAGEngine(project, resolve_ai_config(project_id))
    with _engines_lock:
        _engines[project_id] = (version, engine)
    return engine


_get_engine.cache_clear = _engines.clear  # for modified config/project

if os.environ.get("BEACON_STUDY") == "1":
    from core.study.api import build_router

    _study_router = build_router(_get_engine, _get_engine.cache_clear)
    _shutdown_hooks.append(_study_router.shutdown)
    app.include_router(_study_router)


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
    code: str = ""


class QueryResponse(BaseModel):
    answer: str
    sources: list[SourceItem]


class AIProviderUpdate(BaseModel):
    provider: str
    host: str
    embedding_model: str
    llm_model: str
    llm_num_ctx: Optional[int] = Field(default=None, ge=2048, le=1_000_000)  # None keeps the configured window


class ProjectCreate(BaseModel):
    id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    source_type: str = Field(default="local", pattern="^(local|git)$")
    repo_path: Optional[str] = None  # required if source_type == "local"
    repo_url: Optional[str] = None  # required if source_type == "git"
    auth_token: Optional[str] = None  # optional, only for private git repos
    auto_watch: bool = False


class AutoWatchUpdate(BaseModel):
    enabled: bool


class CleanRequest(BaseModel):
    clone: bool = False
    index: bool = False
    docs: bool = False


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
        raise HTTPException(status_code=409, detail=f"El índice de '{project_id}' está vacío. Corre 'beacon sync {project_id}' primero.")
    try:
        result = engine.ask(req.question, top_k=req.top_k, language=req.language)
    except IndexModelMismatchError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except (BackendUnavailableError, ConnectionError, httpx.TransportError):
        # 503 lets the UI tell "the model server is down" apart from other engine errors.
        ai = engine.ai_config
        raise HTTPException(status_code=503, detail=f"No se pudo conectar al servidor de modelos ({ai.provider}) en '{ai.host}'. Verifica que esté ejecutándose.")
    except BudgetExceededError as e:
        raise HTTPException(status_code=402, detail=str(e))
    except CloudNotConfiguredError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except BackendAuthError as e:
        raise HTTPException(status_code=502, detail=str(e))
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
        "ai_provider": cfg.ai_provider.public_dict(),
        "projects": [p.__dict__ for p in cfg.projects],
    }


@app.put("/config/ai-provider")
def put_ai_provider(body: AIProviderUpdate):
    if body.provider not in {p["id"] for p in available_providers()}:
        raise HTTPException(status_code=422, detail=f"Proveedor desconocido: '{body.provider}'.")
    updated = update_ai_provider(
        provider=body.provider, host=body.host,
        embedding_model=body.embedding_model, llm_model=body.llm_model,
        llm_num_ctx=body.llm_num_ctx,
    )
    _get_engine.cache_clear()
    return updated.__dict__


@app.get("/system/providers")
def system_providers():
    return {"providers": available_providers()}


@app.get("/system/available-models")
def system_available_models(host: Optional[str] = None, provider: Optional[str] = None):
    """Models available on the configured server (or the host and provider passed in the query)."""
    ai = load_config().ai_provider
    provider = provider or ai.provider
    host = host or ai.host
    try:
        models = create_backend(provider, host).list_models()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"No se pudo conectar al servidor de modelos ({provider}) en '{host}': {e}")
    return {"models": models}


def _model_installed(name: str, installed: list) -> bool:
    # Servers such as Ollama list untagged models as "<name>:latest".
    return name in installed or f"{name}:latest" in installed


@app.get("/system/ai-status")
def system_ai_status():
    ai = load_config().ai_provider
    try:
        installed = create_backend(ai.provider, ai.host).list_models(timeout=3)
    except Exception:  # noqa: BLE001
        return {"reachable": False, "llm_model_available": False, "embedding_model_available": False}
    return {
        "reachable": True,
        "llm_model_available": _model_installed(ai.llm_model, installed),
        "embedding_model_available": _model_installed(ai.embedding_model, installed),
    }


BROWSE_ROOT = Path.home().resolve()


@app.get("/system/browse-dirs")
def system_browse_dirs(path: Optional[str] = None):
    """Browses the filesystem of the SERVER running this backend (not the browser's). Limited to
    BROWSE_ROOT (the user's home): without that, anyone with API access could list any folder on disk
    (see docs/en/security.md).
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
    if body.source_type == "local" and not body.repo_path:
        raise HTTPException(status_code=400, detail="'repo_path' es requerido para source_type='local'.")
    if body.source_type == "git" and not body.repo_url:
        raise HTTPException(status_code=400, detail="'repo_url' es requerido para source_type='git'.")
    try:
        entry = services.register_project(
            body.id, name=body.name, auto_watch=body.auto_watch,
            repo_path=body.repo_path if body.source_type == "local" else None,
            repo_url=body.repo_url if body.source_type == "git" else None,
            auth_token=body.auth_token if body.source_type == "git" else None,
        )
    except ProjectAlreadyExistsError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except (InvalidProjectIdError, InvalidRepoUrlError, services.InvalidRequestError, services.CloneError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return entry.__dict__


@app.put("/projects/{project_id}/auto-watch")
def put_auto_watch(project_id: str, body: AutoWatchUpdate):
    if not set_auto_watch(project_id, body.enabled):
        raise HTTPException(status_code=404, detail=f"Proyecto '{project_id}' no encontrado.")
    return {"id": project_id, "auto_watch": body.enabled}


@app.delete("/projects/{project_id}")
def delete_project(project_id: str, purge_data: bool = False):
    # Cached instances have the index open.
    _get_engine.cache_clear()
    try:
        services.unregister_project(project_id, purge_data=purge_data)
    except ProjectBusyError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ProjectNotFoundError:
        raise HTTPException(status_code=404, detail=f"Proyecto '{project_id}' no encontrado.")
    except services.InvalidRequestError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        _get_engine.cache_clear()
    return {"status": "eliminado", "purge_data": purge_data}


@app.get("/system/storage")
def system_storage():
    from core import storage
    projects = [dict(id=u.id, name=u.name, source_type=u.source_type, clone_bytes=u.clone_bytes, index_bytes=u.index_bytes,
                     docs_bytes=u.docs_bytes, total_bytes=u.total_bytes, managed_clone=bool(u.clone_path))
                for u in storage.list_usage()]
    return {"projects": projects, "total_bytes": sum(p["total_bytes"] for p in projects)}


@app.post("/projects/{project_id}/clean")
def clean_project_storage(project_id: str, body: CleanRequest):
    from core import storage
    if not (body.clone or body.index or body.docs):
        raise HTTPException(status_code=400, detail="Indica qué borrar: clone, index y/o docs.")
    _get_engine.cache_clear()
    try:
        freed = storage.clean_project(project_id, clone=body.clone, index=body.index, docs=body.docs)
    except ProjectBusyError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ProjectNotFoundError:
        raise HTTPException(status_code=404, detail=f"Proyecto '{project_id}' no encontrado.")
    except OSError as e:
        raise HTTPException(status_code=409, detail=f"No se pudo borrar (¿está en uso por otro proceso?): {e}")
    finally:
        _get_engine.cache_clear()
    return {"freed_bytes": freed}


def _run_sync(project_id: str):
    """Background task of the "Sync" button and the watcher: pulls remote changes (URL-cloned projects),
    updates the index and the documentation. The status is kept in _sync_status for the UI to poll.
    """
    with _sync_lock:
        _sync_status[project_id] = {"status": "running", "detail": None}
    try:
        result = services.sync_project(project_id, docs=True)
        _get_engine.cache_clear()
        with _sync_lock:
            _sync_status[project_id] = {
                "status": "done", "detail": {"index": result.index, "docs": result.docs, "pulled": result.pulled},
            }
    except ProjectBusyError as e:
        with _sync_lock:
            _sync_status[project_id] = {"status": "error", "detail": str(e), "busy": True}
    except Exception as e:
        logger.exception("sync del proyecto '%s' falló", project_id)
        with _sync_lock:
            _sync_status[project_id] = {"status": "error", "detail": str(e)}


@app.post("/projects/{project_id}/sync", status_code=202)
def trigger_sync(project_id: str, background_tasks: BackgroundTasks):
    _require_project(project_id)
    if not _claim_sync(project_id):
        raise HTTPException(status_code=409, detail="Ya hay un sync en curso para este proyecto.")
    background_tasks.add_task(_run_sync, project_id)
    return {"status": "started"}


def _claim_sync(project_id: str) -> bool:
    """Marks the project as 'running' if it was not, in a single operation under the lock, so two
    consecutive requests cannot start two parallel syncs on the same collection.
    """
    with _sync_lock:
        if _sync_status.get(project_id, {}).get("status") == "running":
            return False
        _sync_status[project_id] = {"status": "running", "detail": None}
        return True


@app.get("/projects/{project_id}/sync-status")
def sync_status(project_id: str):
    with _sync_lock:
        return _sync_status.get(project_id, {"status": "idle", "detail": None})


def _auto_watch_tick():
    """One watcher cycle: syncs the projects with auto_watch=True. Sync is idempotent (it does nothing
    without new commits). Separate from _auto_watch_loop so it can be tested without the infinite sleep.
    """
    try:
        cfg = load_config()
    except Exception:
        logger.exception("auto-watch: no se pudo cargar config.yaml, se reintenta en el próximo ciclo")
        return
    for entry in cfg.projects:
        if not entry.auto_watch:
            continue
        if not _claim_sync(entry.id):
            continue
        try:
            _run_sync(entry.id)
        except Exception as e:  # _run_sync already logs its errors; this is a safety net
            logger.exception("auto-watch: falló el sync automático del proyecto '%s'", entry.id)
            with _sync_lock:  # release the claim, or the project would stay "running" forever
                _sync_status[entry.id] = {"status": "error", "detail": str(e)}
            continue
        with _sync_lock:
            if _sync_status.get(entry.id, {}).get("busy"):
                # Another console was syncing it: not a watcher error; release without marking anything and retry later.
                logger.info("auto-watch: '%s' se está sincronizando en otro proceso, se reintenta en el próximo ciclo", entry.id)
                _sync_status.pop(entry.id, None)


def _auto_watch_loop():
    while True:
        time.sleep(AUTO_WATCH_INTERVAL_SECONDS)
        _auto_watch_tick()


_auto_watch_thread = threading.Thread(target=_auto_watch_loop, daemon=True)
_auto_watch_thread.start()


_FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


def _resolve_frontend_file(full_path: str, dist: Path = _FRONTEND_DIST) -> Optional[Path]:
    """Static file requested by the SPA, or None if it does not exist or the path tries to leave dist/
    (e.g. '/%2e%2e/config/credentials.yaml': uvicorn decodes %2e but does not normalize '..').
    """
    if not full_path:
        return None
    root = dist.resolve()
    candidate = (root / full_path).resolve()
    if not candidate.is_relative_to(root) or not candidate.is_file():
        return None
    return candidate


if _FRONTEND_DIST.exists():
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    app.mount("/assets", StaticFiles(directory=str(_FRONTEND_DIST / "assets")), name="frontend-assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa_fallback(full_path: str):
        return FileResponse(_resolve_frontend_file(full_path) or (_FRONTEND_DIST / "index.html"))
else:
    from fastapi.responses import HTMLResponse

    @app.get("/", include_in_schema=False)
    def ui_not_built():
        return HTMLResponse(
            "<h1>Beacon: la API está funcionando</h1>"
            "<p>La interfaz web todavía no está compilada. Desde la raíz del proyecto:</p>"
            "<pre>cd frontend\nnpm install\nnpm run build</pre>"
            "<p>Luego reinicia <code>beacon serve</code>. Mientras tanto puedes usar la CLI "
            "o explorar la API en <a href=\"/docs\">/docs</a>.</p>",
            status_code=503,
        )
