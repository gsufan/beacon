"""API REST multi-proyecto. La UI (frontend/dist) se sirve como estáticos, ver el final del archivo."""

import logging
import os
import threading
import time
from functools import lru_cache
from pathlib import Path
from typing import Optional

import git
import ollama
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

logger = logging.getLogger("beacon.api")

from core.config import (
    InvalidProjectIdError,
    ProjectAlreadyExistsError,
    ProjectEntry,
    add_project,
    validate_project_id,
    delete_project_token,
    get_project_token,
    load_config,
    remove_project,
    set_auto_watch,
    set_project_token,
    update_ai_provider,
)
from core.projects import (
    DATA_ROOT,
    InvalidRepoUrlError,
    build_clone_url,
    ensure_project_dirs,
    get_project,
    list_projects,
    safe_project_dir,
    validate_repo_url,
    ProjectNotFoundError,
)
from core.project_lock import ProjectBusyError, ensure_not_busy, project_lock
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

# API key opcional: si BEACON_API_KEY está seteada, se exige en el header
# X-API-Key para los endpoints de datos/config (no para servir la SPA ni
# para /healthz). Sin la env var (default), la API queda abierta — igual
# que hoy — pensada para uso local/interno; ver README para exponerla
# de forma más segura.
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
    """Sin autenticación ni dependencias externas — para healthchecks de Docker/orquestadores."""
    return {"status": "ok"}


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
        raise HTTPException(status_code=409, detail=f"El índice de '{project_id}' está vacío. Corre 'beacon sync {project_id}' primero.")
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


def _validate_repo_url(repo_url: str):
    """Traduce la validación compartida (core.projects) a un 400 de la API."""
    try:
        validate_repo_url(repo_url)
    except InvalidRepoUrlError as e:
        raise HTTPException(status_code=400, detail=str(e))


def _clone_url(repo_url: str, auth_token: Optional[str]) -> str:
    return build_clone_url(repo_url, auth_token)


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
    try:
        validate_project_id(body.id)
    except InvalidProjectIdError as e:
        raise HTTPException(status_code=400, detail=str(e))
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
    if purge_data:
        try:  # no borrar data/<id>/ mientras otro proceso la está escribiendo
            ensure_not_busy(safe_project_dir(project_id, DATA_ROOT))
        except ProjectBusyError as e:
            raise HTTPException(status_code=409, detail=str(e))
        except ValueError:
            pass  # id inválido: remove_project/safe_project_dir lo reportan abajo
    if not remove_project(project_id):
        raise HTTPException(status_code=404, detail=f"Proyecto '{project_id}' no encontrado.")
    _get_engine.cache_clear()
    delete_project_token(project_id)
    if purge_data:
        import shutil
        try:
            project_dir = safe_project_dir(project_id, DATA_ROOT)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"Proyecto desregistrado, pero no se purgaron datos: {e}")
        if project_dir.exists():
            shutil.rmtree(project_dir)
    return {"status": "eliminado", "purge_data": purge_data}


def _run_sync(project_id: str):
    with _sync_lock:
        _sync_status[project_id] = {"status": "running", "detail": None}
    try:
        project = get_project(project_id)
        cfg = load_config()
        # _claim_sync solo coordina dentro de este proceso; el lock de archivo
        # coordina además con la CLI ('beacon sync'/'docs' en otra consola).
        with project_lock(Path(project.chroma_dir).parent):
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
    if not _claim_sync(project_id):
        raise HTTPException(status_code=409, detail="Ya hay un sync en curso para este proyecto.")
    background_tasks.add_task(_run_sync, project_id)
    return {"status": "started"}


def _claim_sync(project_id: str) -> bool:
    """Marca el proyecto como 'running' si no lo estaba, en una sola operación
    bajo el lock: evita que dos pedidos seguidos lancen dos syncs en paralelo
    sobre la misma colección (la tarea en background arranca después)."""
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
    """Un ciclo del watcher: sincroniza los proyectos con auto_watch=True.
    sync() ya es idempotente (no hace nada si no hay commits nuevos), así
    que llamarlo periódicamente es seguro. Separado de _auto_watch_loop
    para poder probarlo sin depender del sleep infinito.
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
            # Si otra consola está sincronizando este proyecto, ni siquiera
            # hacer 'git pull' (cambiaría archivos a mitad de su indexado):
            # se salta y se reintenta en el próximo ciclo.
            ensure_not_busy(safe_project_dir(entry.id, DATA_ROOT))
        except ProjectBusyError:
            logger.info("auto-watch: '%s' se está sincronizando en otro proceso, se reintenta en el próximo ciclo", entry.id)
            with _sync_lock:
                _sync_status.pop(entry.id, None)  # liberar el claim sin marcar error
            continue
        except ValueError:
            pass  # id inválido: que lo reporte _run_sync como cualquier otro error
        try:
            if entry.source_type == "git":
                _pull_git_project(entry)
            _run_sync(entry.id)
        except Exception as e:
            logger.exception("auto-watch: falló el sync automático del proyecto '%s'", entry.id)
            with _sync_lock:  # liberar el claim, o el proyecto quedaría "running" para siempre
                _sync_status[entry.id] = {"status": "error", "detail": str(e)}
            continue


def _auto_watch_loop():
    while True:
        time.sleep(AUTO_WATCH_INTERVAL_SECONDS)
        _auto_watch_tick()


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


def _resolve_frontend_file(full_path: str, dist: Path = _FRONTEND_DIST) -> Optional[Path]:
    """Archivo estático pedido por la SPA, o None si no existe o si la ruta
    intenta salir de dist/ (ej. '/%2e%2e/config/credentials.yaml': uvicorn
    decodifica el %2e pero no normaliza los '..')."""
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
    # Instalación recién clonada sin compilar la UI: en vez de un 404 mudo en
    # la raíz, explicar qué falta (la API sigue funcionando igual).
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
