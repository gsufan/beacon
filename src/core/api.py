"""Beacon REST API. It also serves the compiled web interface (frontend/dist)."""

import hmac
import logging
import os
import re
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from core import services, storage
from core.config import (
    InvalidProjectIdError,
    ProjectAlreadyExistsError,
    load_config,
    set_auto_watch,
    update_ai_provider,
)
from core.engine.backends import (
    BackendAuthError, BackendUnavailableError, ExternalServerError, available_providers, create_backend, model_available,
)
from core.engine.chroma_utils import read_index_version
from core.engine.indexer import IndexModelMismatchError
from core.engine.rag_engine import RAGEngine
from core.project_lock import ProjectBusyError
from core.projects import InvalidRepoUrlError, ProjectNotFoundError, get_project, list_projects
from core.version import VERSION

logger = logging.getLogger("beacon.api")

AUTO_WATCH_INTERVAL_SECONDS = 300

_shutdown_hooks: list = []


@asynccontextmanager
async def _lifespan(_app):
    stop = threading.Event()
    threading.Thread(target=_auto_watch_loop, args=(stop,), daemon=True, name="beacon-auto-watch").start()
    yield
    stop.set()
    for hook in _shutdown_hooks:  # stop whatever is in progress (e.g. a model answer)
        hook()


app = FastAPI(title="Beacon API", version=VERSION, docs_url="/api/docs", redoc_url="/api/redoc", lifespan=_lifespan)
# The compiled interface is served from the same origin as the API; the only legitimate cross-origin
# is the Vite dev server. "*" is not used (see docs/en/security.md).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Optional keys, sent in the X-API-Key header (the interface and /healthz stay open). BEACON_API_KEY gives full access;
# BEACON_READ_KEY only reads and asks questions, so a person who only consults cannot delete a project or change the
# model server. With neither set, the API is open.
API_KEY = os.environ.get("BEACON_API_KEY")
READ_KEY = os.environ.get("BEACON_READ_KEY")
_PROTECTED_PREFIXES = ("/projects", "/config", "/system")
# Reads that the consultation key may not do: browsing the server's folders and making it query other hosts.
_ADMIN_READS = ("/system/browse-dirs", "/system/available-models")
_QUERY_PATH = re.compile(r"^/projects/[^/]+/query$")


def _same(given: str, expected: Optional[str]) -> bool:
    return bool(expected) and hmac.compare_digest(given.encode(), expected.encode())


def _read_only_allowed(request: Request) -> bool:
    path = request.url.path
    if request.method in ("GET", "HEAD"):
        return not path.startswith(_ADMIN_READS)
    return request.method == "POST" and bool(_QUERY_PATH.match(path))


@app.middleware("http")
async def api_key_middleware(request: Request, call_next):
    if (API_KEY or READ_KEY) and request.url.path.startswith(_PROTECTED_PREFIXES):
        given = request.headers.get("x-api-key", "")
        if _same(given, API_KEY):
            return await call_next(request)
        if _same(given, READ_KEY):
            if _read_only_allowed(request):
                return await call_next(request)
            return JSONResponse({"detail": "La clave de consulta no permite modificar proyectos ni la configuración."}, status_code=403)
        return JSONResponse({"detail": "Falta o es inválido el header X-API-Key."}, status_code=401)
    return await call_next(request)


# Domain errors and the HTTP status each one maps to. The endpoints let them propagate.
_ERROR_STATUS = {
    ProjectNotFoundError: 404,
    InvalidProjectIdError: 400,
    InvalidRepoUrlError: 400,
    services.InvalidRequestError: 400,
    services.CloneError: 400,
    ProjectAlreadyExistsError: 409,
    ProjectBusyError: 409,
    IndexModelMismatchError: 409,
    ExternalServerError: 409,
    BackendAuthError: 502,
}
_DOMAIN_ERRORS = tuple(_ERROR_STATUS)


def _domain_error_handler(status_code: int):
    def handler(_request: Request, exc: Exception):
        return JSONResponse({"detail": str(exc)}, status_code=status_code)
    return handler


for _error, _status in _ERROR_STATUS.items():
    app.add_exception_handler(_error, _domain_error_handler(_status))


def _model_server_down(provider: str, host: str) -> HTTPException:
    # 503 lets the UI tell "the model server is down" apart from other engine errors.
    return HTTPException(status_code=503, detail=f"No se pudo conectar al servidor de modelos ({provider}) en '{host}'. "
                                                 "Verifica que esté ejecutándose.")


@app.get("/healthz", include_in_schema=False)
def healthz():
    """No authentication and no external dependencies, for Docker and orchestrator healthchecks."""
    return {"status": "ok"}


class EngineCache:
    """One RAGEngine per project, reused across requests while the index does not change. If it changed (a
    sync in this process or in another, e.g. `beacon sync` in a console), a new one is created that sees
    the updated index (see chroma_utils).
    """

    def __init__(self):
        self._engines: dict = {}  # project_id -> (index version, RAGEngine)
        self._lock = threading.Lock()

    def get(self, project_id: str) -> RAGEngine:
        project = get_project(project_id)
        version = read_index_version(Path(project.chroma_dir).parent)
        with self._lock:
            cached = self._engines.get(project_id)
            if cached and cached[0] == version:
                return cached[1]
        engine = RAGEngine(project, load_config().ai_provider)
        with self._lock:
            self._engines[project_id] = (version, engine)
        return engine

    def clear(self):
        """For a modified configuration or project, and before deleting data: cached engines keep the index open."""
        with self._lock:
            self._engines.clear()


_engines = EngineCache()


def _get_engine(project_id: str) -> RAGEngine:
    return _engines.get(project_id)


if os.environ.get("BEACON_STUDY") == "1":
    from core.study.api import build_router

    _study_router = build_router(lambda project_id: _get_engine(project_id), _engines.clear)
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
    return {"status": "ok", "total_chunks_indexados": _get_engine(project_id).collection.count()}


@app.post("/projects/{project_id}/query", response_model=QueryResponse)
def query(project_id: str, req: QueryRequest):
    engine = _get_engine(project_id)
    if engine.collection.count() == 0:
        raise HTTPException(status_code=409, detail=f"El índice de '{project_id}' está vacío. Corre 'beacon sync {project_id}' primero.")
    try:
        result = engine.ask(req.question, top_k=req.top_k, language=req.language)
    except _DOMAIN_ERRORS:
        raise
    except (BackendUnavailableError, ConnectionError, httpx.TransportError):
        raise _model_server_down(engine.ai_config.provider, engine.ai_config.host)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error en el motor RAG: {e}")
    return result.to_dict()


@app.get("/projects/{project_id}/docs/tree")
def docs_tree(project_id: str):
    docs_dir = Path(get_project(project_id).docs_dir)
    if not docs_dir.exists():
        return {"files": []}
    files = sorted(
        p.relative_to(docs_dir).as_posix()[: -len(".md")]
        for p in docs_dir.rglob("*.md")
    )
    return {"files": files}


@app.get("/projects/{project_id}/docs")
def docs(project_id: str, file_path: str):
    docs_dir = Path(get_project(project_id).docs_dir).resolve()
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
    _engines.clear()
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


@app.get("/system/ai-status")
def system_ai_status():
    ai = load_config().ai_provider
    try:
        installed = create_backend(ai.provider, ai.host).list_models(timeout=3)
    except Exception:  # noqa: BLE001
        return {"reachable": False, "llm_model_available": False, "embedding_model_available": False}
    return {
        "reachable": True,
        "llm_model_available": model_available(ai.llm_model, installed),
        "embedding_model_available": model_available(ai.embedding_model, installed),
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
    entry = services.register_project(
        body.id, name=body.name, auto_watch=body.auto_watch,
        repo_path=body.repo_path if body.source_type == "local" else None,
        repo_url=body.repo_url if body.source_type == "git" else None,
        auth_token=body.auth_token if body.source_type == "git" else None,
    )
    return entry.__dict__


@app.put("/projects/{project_id}/auto-watch")
def put_auto_watch(project_id: str, body: AutoWatchUpdate):
    if not set_auto_watch(project_id, body.enabled):
        raise HTTPException(status_code=404, detail=f"Proyecto '{project_id}' no encontrado.")
    return {"id": project_id, "auto_watch": body.enabled}


@app.delete("/projects/{project_id}")
def delete_project(project_id: str, purge_data: bool = False):
    _engines.clear()
    try:
        services.unregister_project(project_id, purge_data=purge_data)
    finally:
        _engines.clear()
    return {"status": "eliminado", "purge_data": purge_data}


@app.get("/system/storage")
def system_storage():
    projects = [dict(id=u.id, name=u.name, source_type=u.source_type, clone_bytes=u.clone_bytes, index_bytes=u.index_bytes,
                     docs_bytes=u.docs_bytes, total_bytes=u.total_bytes, managed_clone=bool(u.clone_path))
                for u in storage.list_usage()]
    return {"projects": projects, "total_bytes": sum(p["total_bytes"] for p in projects)}


@app.post("/projects/{project_id}/clean")
def clean_project_storage(project_id: str, body: CleanRequest):
    if not (body.clone or body.index or body.docs):
        raise HTTPException(status_code=400, detail="Indica qué borrar: clone, index y/o docs.")
    _engines.clear()
    try:
        freed = storage.clean_project(project_id, clone=body.clone, index=body.index, docs=body.docs)
    except _DOMAIN_ERRORS:
        raise
    except OSError as e:
        raise HTTPException(status_code=409, detail=f"No se pudo borrar (¿está en uso por otro proceso?): {e}")
    finally:
        _engines.clear()
    return {"freed_bytes": freed}


_sync_status: dict = {}
_sync_lock = threading.Lock()


def _run_sync(project_id: str):
    """Background task of the "Sync" button and the watcher: pulls remote changes (URL-cloned projects),
    updates the index and the documentation. The status is kept in _sync_status for the UI to poll.
    """
    with _sync_lock:
        _sync_status[project_id] = {"status": "running", "detail": None}
    try:
        result = services.sync_project(project_id, docs=True)
        _engines.clear()
        with _sync_lock:
            _sync_status[project_id] = {
                "status": "done", "detail": {"index": result.index.to_dict(), "docs": result.docs.to_dict() if result.docs else None,
                           "pulled": result.pulled},
            }
    except ProjectBusyError as e:
        with _sync_lock:
            _sync_status[project_id] = {"status": "error", "detail": str(e), "busy": True}
    except Exception as e:
        logger.exception("sync del proyecto '%s' falló", project_id)
        with _sync_lock:
            _sync_status[project_id] = {"status": "error", "detail": str(e)}


def _claim_sync(project_id: str) -> bool:
    """Marks the project as 'running' if it was not, in a single operation under the lock, so two
    consecutive requests cannot start two parallel syncs on the same collection.
    """
    with _sync_lock:
        if _sync_status.get(project_id, {}).get("status") == "running":
            return False
        _sync_status[project_id] = {"status": "running", "detail": None}
        return True


@app.post("/projects/{project_id}/sync", status_code=202)
def trigger_sync(project_id: str, background_tasks: BackgroundTasks):
    get_project(project_id)
    if not _claim_sync(project_id):
        raise HTTPException(status_code=409, detail="Ya hay un sync en curso para este proyecto.")
    background_tasks.add_task(_run_sync, project_id)
    return {"status": "started"}


@app.get("/projects/{project_id}/sync-status")
def sync_status(project_id: str):
    with _sync_lock:
        return _sync_status.get(project_id, {"status": "idle", "detail": None})


def _auto_watch_tick():
    """One watcher cycle: syncs the projects with auto_watch=True. Sync is idempotent (it does nothing
    without new commits). Separate from _auto_watch_loop so it can be tested without waiting.
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


def _auto_watch_loop(stop: threading.Event):
    """Runs while the server is up (started and stopped by the app lifespan)."""
    while not stop.wait(AUTO_WATCH_INTERVAL_SECONDS):
        _auto_watch_tick()


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
        # no-cache: a browser that kept the old index.html would load assets of a previous build after an update
        return FileResponse(_resolve_frontend_file(full_path) or (_FRONTEND_DIST / "index.html"), headers={"Cache-Control": "no-cache"})
else:
    from fastapi.responses import HTMLResponse

    @app.get("/", include_in_schema=False)
    def ui_not_built():
        return HTMLResponse(
            "<h1>Beacon: la API está funcionando</h1>"
            "<p>La interfaz web todavía no está compilada. Desde la raíz del proyecto:</p>"
            "<pre>cd frontend\nnpm install\nnpm run build</pre>"
            "<p>Luego reinicia <code>beacon serve</code>. Mientras tanto puedes usar la CLI "
            "o explorar la API en <a href=\"/api/docs\">/api/docs</a>.</p>",
            status_code=503,
        )
