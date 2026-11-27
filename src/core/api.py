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
import uuid

import git
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from core import assist, edition, expertise, insight, instance, services, storage, team, verification
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
from core.engine.embeddings import embed_query
from core.engine.rag_engine import RAGEngine
from core.monitor import ModelMonitor
from core.project_lock import ProjectBusyError
from core.projects import InvalidRepoUrlError, ProjectNotFoundError, get_project, list_projects
from core.version import VERSION
from hub.security import InvalidTokenError

logger = logging.getLogger("beacon.api")

AUTO_WATCH_INTERVAL_SECONDS = 300
# Synthetic check of the model server (core/monitor.py); 0 disables it. Off in study mode, where it would add load
# in the middle of a timed question.
MONITOR_INTERVAL_SECONDS = float(os.environ.get("BEACON_MONITOR_SECONDS", "300"))

_shutdown_hooks: list = []


@asynccontextmanager
async def _lifespan(_app):
    stop = threading.Event()
    threading.Thread(target=_auto_watch_loop, args=(stop,), daemon=True, name="beacon-auto-watch").start()
    if team.hub_url():
        threading.Thread(target=instance.run, args=(stop, _instance_settings, VERSION), daemon=True, name="beacon-instance").start()
    if MONITOR_INTERVAL_SECONDS > 0 and os.environ.get("BEACON_STUDY") != "1":
        threading.Thread(target=_monitor.run, args=(stop, MONITOR_INTERVAL_SECONDS), daemon=True, name="beacon-monitor").start()
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


def _hub_settings():
    """Team mode if a Hub is configured; a Hub that cannot be reached for its key is reported, not ignored."""
    try:
        return team.hub_settings()
    except (httpx.HTTPError, KeyError, ValueError) as e:
        raise HTTPException(status_code=503, detail=f"No se pudo contactar al Beacon Hub configurado: {e}")


def _instance_settings():
    try:
        return team.hub_settings()
    except (httpx.HTTPError, KeyError, ValueError):
        return None


@app.middleware("http")
async def access_middleware(request: Request, call_next):
    if not request.url.path.startswith(_PROTECTED_PREFIXES):
        return await call_next(request)
    try:
        instance.check()
    except instance.InstanceBlockedError as e:
        return JSONResponse({"detail": str(e), "instance_blocked": True}, status_code=403)
    try:
        settings = _hub_settings()
    except HTTPException as e:
        return JSONResponse({"detail": e.detail}, status_code=e.status_code)
    if settings is not None:
        return await _team_access(request, call_next, settings)
    return await _api_key_access(request, call_next)


async def _team_access(request: Request, call_next, settings):
    """Team mode: a session signed by the Hub, a valid license and the Scrum role the route requires."""
    authorization = request.headers.get("authorization", "")
    try:
        claims = team.verify_session(settings, authorization)
    except InvalidTokenError:
        return JSONResponse({"detail": "Inicia sesión con tu cuenta de Beacon."}, status_code=401)
    try:
        team.verify_license(settings, request.headers.get("x-beacon-license", ""), claims["org"])
    except team.LicenseExpiredError as e:
        return JSONResponse({"detail": str(e)}, status_code=402)
    action, project_id = team.required_permission(request.method, request.url.path)
    if not team.allowed(claims, action, project_id):
        return JSONResponse({"detail": "Tu rol no permite esta acción en este proyecto."}, status_code=403)
    request.state.user = claims
    response = await call_next(request)
    if response.status_code < 400:
        event = team.audit_action(request.method, request.url.path)
        if event:
            team.report(settings, authorization, *event)
    return response


async def _api_key_access(request: Request, call_next):
    if API_KEY or READ_KEY:
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
_monitor = ModelMonitor(lambda: load_config().ai_provider)


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
    verified: Optional[dict] = None


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


@app.get("/auth/status")
def auth_status():
    """Whether this instance works in team mode (with a Hub) or on its own, its edition and installation state."""
    url = team.hub_url()
    state = instance.read_state()
    try:
        instance.check()
        blocked = None
    except instance.InstanceBlockedError as e:
        blocked = str(e)
    return {"mode": "team" if url else "standalone", "hub_url": url, "edition": edition.EDITION,
            "instance": {"registered": instance.credentials() is not None, "revoked": bool(state.get("revoked")),
                         "lease_exp": state.get("lease_exp"), "blocked": blocked}}


class InstanceRegisterRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    personal: bool = False


@app.post("/auth/instance")
def register_instance(body: InstanceRegisterRequest, request: Request):
    """Registers this Beacon Local with the Hub, using the session of the person who asks (an administrator for a
    shared server, anyone for their personal installation), and obtains the first lease."""
    settings = _hub_settings()
    if settings is None:
        raise HTTPException(status_code=409, detail="Esta instancia no está conectada a un Beacon Hub.")
    if instance.credentials() is not None and not instance.read_state().get("revoked"):
        raise HTTPException(status_code=409, detail="Esta instalación ya está registrada.")
    try:
        res = httpx.post(f"{settings.url}/instances", json=body.model_dump(),
                         headers={"Authorization": request.headers.get("authorization", "")}, timeout=_HUB_TIMEOUT)
    except httpx.HTTPError as e:
        raise HTTPException(status_code=503, detail=f"No se pudo contactar al Beacon Hub: {e}")
    if res.status_code != 201:
        raise HTTPException(status_code=res.status_code, detail=res.json().get("detail", "No se pudo registrar."))
    data = res.json()
    instance.save_credentials(data["instance_id"], data["secret"], settings.url)
    try:
        instance.heartbeat(settings.url, settings.public_key, VERSION)
    except (httpx.HTTPError, InvalidTokenError) as e:
        raise HTTPException(status_code=503, detail=f"La instalación se registró, pero no se obtuvo la licencia: {e}")
    return {"status": "ok", "instance_id": data["instance_id"]}


_HUB_TIMEOUT = 20


@app.api_route("/hub/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"], include_in_schema=False)
async def hub_proxy(path: str, request: Request):
    """Forwards the interface's calls to the Hub (accounts, team, license, GitHub), so the browser only talks to
    Beacon Local. The Hub checks the session itself.
    """
    url = team.hub_url()
    if not url:
        raise HTTPException(status_code=404, detail="Esta instancia no está conectada a un Beacon Hub.")
    headers = {k: v for k, v in request.headers.items() if k.lower() in ("authorization", "content-type")}
    try:
        async with httpx.AsyncClient(timeout=_HUB_TIMEOUT) as client:
            res = await client.request(request.method, f"{url}/{path}", params=request.query_params,
                                       content=await request.body(), headers=headers)
    except httpx.HTTPError as e:
        raise HTTPException(status_code=503, detail=f"No se pudo contactar al Beacon Hub: {e}")
    try:
        body = res.json()
    except ValueError:
        body = {"detail": res.text[:300]}
    return JSONResponse(body, status_code=res.status_code)


@app.get("/projects")
def get_projects(request: Request):
    user = getattr(request.state, "user", None)
    projects = list_projects()
    if user and not user.get("admin"):  # team mode: only the projects where the person has a role
        projects = [p for p in projects if p.id in (user.get("roles") or {})]
    return [{"id": p.id, "name": p.name} for p in projects]


@app.get("/projects/{project_id}/health")
def project_health(project_id: str):
    return {"status": "ok", "total_chunks_indexados": _get_engine(project_id).collection.count()}


@app.post("/projects/{project_id}/query", response_model=QueryResponse)
def query(project_id: str, req: QueryRequest):
    engine = _get_engine(project_id)
    if engine.collection.count() == 0:
        raise HTTPException(status_code=409, detail=f"El índice de '{project_id}' está vacío. Corre 'beacon sync {project_id}' primero.")
    verified = None
    key = _hub_public_key()
    if key:
        try:
            emb = embed_query(engine.backend, engine.ai_config.embedding_model, req.question)
            project = get_project(project_id)
            verified = verification.match(Path(project.docs_dir).parent, project.repo_path, emb, key)
        except Exception:  # noqa: BLE001 - a verified answer is a bonus; the normal answer must not fail because of it
            logger.exception("búsqueda de respuestas verificadas falló")
    try:
        result = engine.ask(req.question, top_k=req.top_k, language=req.language)
    except _DOMAIN_ERRORS:
        raise
    except (BackendUnavailableError, ConnectionError, httpx.TransportError):
        raise _model_server_down(engine.ai_config.provider, engine.ai_config.host)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error en el motor RAG: {e}")
    out = result.to_dict()
    try:
        insight.record_usage(Path(get_project(project_id).docs_dir).parent, out["sources"], out["answer"])
    except Exception:  # noqa: BLE001 - the usage log must never break an answer
        logger.warning("no se pudo registrar el uso del proyecto %s", project_id)
    return {**out, "verified": verified}


class StartIssueRequest(BaseModel):
    language: str = Field(default="es", pattern="^(es|en)$")


@app.post("/projects/{project_id}/issues/{ref}/start")
def start_issue(project_id: str, ref: str, req: StartIssueRequest, request: Request):
    """«Where do I start with this issue?»: takes the issue's title and description from the project's backlog (GitHub
    or Jira, through the Hub) and asks the code which files and functions to review, with sources.
    """
    url = team.hub_url()
    if not url:
        raise HTTPException(status_code=409, detail="Las issues requieren conectar Beacon a un Beacon Hub.")
    try:
        res = httpx.get(f"{url}/projects/{project_id}/issues/{ref}",
                        headers={"Authorization": request.headers.get("authorization", "")}, timeout=_HUB_TIMEOUT)
    except httpx.HTTPError as e:
        raise HTTPException(status_code=503, detail=f"No se pudo contactar al Beacon Hub: {e}")
    if res.status_code != 200:
        raise HTTPException(status_code=res.status_code, detail=res.json().get("detail", "No se pudo leer la issue."))
    issue = res.json()
    answer = query(project_id, QueryRequest(question=team.issue_question(issue, req.language), top_k=8, language=req.language))
    return {"issue": {k: issue.get(k) for k in ("id", "ref", "source", "number", "title", "url", "labels")}, **answer}


def _fetch_issue(request: Request, project_id: str, ref: str) -> dict:
    url = team.hub_url()
    if not url:
        raise HTTPException(status_code=409, detail="Las issues requieren conectar Beacon a un Beacon Hub.")
    try:
        res = httpx.get(f"{url}/projects/{project_id}/issues/{ref}", headers={"Authorization": request.headers.get("authorization", "")},
                        timeout=_HUB_TIMEOUT)
    except httpx.HTTPError as e:
        raise HTTPException(status_code=503, detail=f"No se pudo contactar al Beacon Hub: {e}")
    if res.status_code != 200:
        raise HTTPException(status_code=res.status_code, detail=res.json().get("detail", "No se pudo leer la issue."))
    return res.json()


def _model_call(fn):
    try:
        return fn()
    except _DOMAIN_ERRORS:
        raise
    except (BackendUnavailableError, ConnectionError, httpx.TransportError):
        ai = load_config().ai_provider
        raise _model_server_down(ai.provider, ai.host)


@app.post("/projects/{project_id}/issues/{ref}/plan")
def issue_plan(project_id: str, ref: str, req: StartIssueRequest, request: Request):
    """Implementation plan for an issue: steps, files and functions to change, tests, risks and a size estimate."""
    issue = _fetch_issue(request, project_id, ref)
    answer = query(project_id, QueryRequest(question=assist.plan_question(issue, req.language), top_k=8, language=req.language))
    return {"issue": {k: issue.get(k) for k in ("id", "ref", "source", "title", "url")}, **answer}


@app.get("/projects/{project_id}/issues/{ref}/code")
def issue_linked_code(project_id: str, ref: str):
    """Commits that mention the issue and the files they changed: the code that implemented (or is implementing) it."""
    try:
        return insight.issue_code(get_project(project_id).repo_path, ref)
    except git.GitError as e:
        raise HTTPException(status_code=409, detail=f"No se pudo leer el historial de git del proyecto: {e}")


class RelatedRequest(BaseModel):
    issue: dict
    candidates: list = Field(default_factory=list, max_length=200)


@app.post("/projects/{project_id}/issues/related")
def issues_related(project_id: str, body: RelatedRequest):
    """Similar issues (open or already solved), as a suggestion so nobody repeats work. It never blocks anything."""
    engine = _get_engine(project_id)
    return {"related": _model_call(lambda: assist.related_issues(engine, body.issue, body.candidates))}


class ImpactRequest(BaseModel):
    targets: list[str] = Field(..., min_length=1, max_length=10)


@app.post("/projects/{project_id}/impact")
def change_impact(project_id: str, body: ImpactRequest):
    """What depends on these functions: direct and indirect callers, the tests that exercise them, who knows the
    affected files and which verified documents a change would leave pending."""
    engine = _get_engine(project_id)
    project = get_project(project_id)
    result = insight.impact(engine.collection, body.targets)
    try:
        result["experts"] = expertise.experts(project.repo_path, result["files"][:10])["overall"] if result["files"] else []
    except (git.GitError, expertise.InvalidPathError):
        result["experts"] = []
    key = _hub_public_key()
    docs_dir = Path(project.docs_dir)
    result["docs"] = [{"file_path": f, **verification.doc_status(docs_dir.parent, docs_dir, f, key)} for f in result["files"]
                      if (docs_dir / f"{f}.md").exists()]
    return result


class QAPlanRequest(BaseModel):
    targets: list[str] = Field(..., min_length=1, max_length=6)
    issue: Optional[dict] = None
    language: str = Field(default="es", pattern="^(es|en)$")


@app.post("/projects/{project_id}/qa-plan")
def qa_test_plan(project_id: str, body: QAPlanRequest):
    """«What should we test?»: test cases for a change, from its impact and the existing tests (for QA)."""
    engine = _get_engine(project_id)
    result = insight.impact(engine.collection, body.targets)
    plan = _model_call(lambda: assist.qa_plan(engine, result, body.issue, body.language))
    return {"impact": result, "plan": plan}


class HandoverRequest(BaseModel):
    person: str = Field(..., min_length=1, max_length=80)
    git_author: Optional[str] = None
    issues: list = Field(default_factory=list, max_length=100)
    commits: list = Field(default_factory=list, max_length=200)
    language: str = Field(default="es", pattern="^(es|en)$")


@app.post("/projects/{project_id}/handover")
def handover(project_id: str, body: HandoverRequest):
    """Summary of what a person leaves in progress, for whoever picks up their work."""
    engine = _get_engine(project_id)
    local = []
    author = body.git_author or body.person
    try:
        repo = git.Repo(get_project(project_id).repo_path)
        raw = repo.git.log("--no-merges", "-n40", f"--author={author}", "--name-only", "--format=@@%aI\t%s")
        for line in raw.splitlines():
            if line.startswith("@@"):
                date, msg = line[2:].split("\t", 1)
                local.append({"date": date, "message": msg[:200], "files": []})
            elif line.strip() and local:
                local[-1]["files"].append(line.strip())
    except git.GitError:
        pass
    summary = _model_call(lambda: assist.handover_summary(engine, body.person, body.issues, body.commits, local, body.language))
    return {"summary": summary, "local_commits": local[:15]}


@app.get("/projects/{project_id}/knowledge")
def knowledge(project_id: str, depth: int = 2):
    """Who knows each module and its bus factor, from the git history (names and counts only)."""
    try:
        return insight.knowledge_map(get_project(project_id).repo_path, max(1, min(depth, 4)))
    except git.GitError as e:
        raise HTTPException(status_code=409, detail=f"No se pudo leer el historial de git del proyecto: {e}")


@app.get("/projects/{project_id}/summary")
def sprint_summary(project_id: str, days: int = 14):
    """What changed in the code in the last days, grouped by issue: material for the sprint review."""
    try:
        return insight.sprint_summary(get_project(project_id).repo_path, max(1, min(days, 90)))
    except git.GitError as e:
        raise HTTPException(status_code=409, detail=f"No se pudo leer el historial de git del proyecto: {e}")


@app.get("/projects/{project_id}/insights")
def project_insights(project_id: str):
    """Documentation gaps and the radar of code that is hard to understand, from the anonymous usage per module."""
    project = get_project(project_id)
    docs_dir = Path(project.docs_dir)
    key = _hub_public_key()
    files = [p.relative_to(docs_dir).as_posix()[: -len(".md")] for p in docs_dir.rglob("*.md")
             if not p.name.endswith(".draft.md") and p.name != "OVERVIEW.md"] if docs_dir.exists() else []
    reviews = {f: verification.doc_status(docs_dir.parent, docs_dir, f, key)["status"] for f in files}
    return insight.insights(docs_dir.parent, docs_dir, reviews)


@app.get("/projects/{project_id}/experts")
def project_experts(project_id: str, path: list[str] = Query(default=[])):
    """Who changed the given files most, from the local git history (names and counts, no emails)."""
    if not path:
        raise HTTPException(status_code=400, detail="Indica al menos un archivo (parámetro path).")
    try:
        return expertise.experts(get_project(project_id).repo_path, path)
    except expertise.InvalidPathError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except git.GitError as e:
        raise HTTPException(status_code=409, detail=f"No se pudo leer el historial de git del proyecto: {e}")


@app.get("/projects/{project_id}/docs/tree")
def docs_tree(project_id: str):
    docs_dir = Path(get_project(project_id).docs_dir)
    if not docs_dir.exists():
        return {"files": []}
    files = sorted(
        p.relative_to(docs_dir).as_posix()[: -len(".md")]
        for p in docs_dir.rglob("*.md") if not p.name.endswith(".draft.md")
    )
    key = _hub_public_key()
    project_dir = docs_dir.parent
    reviews = {f: verification.doc_status(project_dir, docs_dir, f, key)["status"] for f in files} if key else {}
    return {"files": files, "reviews": reviews}


@app.get("/projects/{project_id}/docs")
def docs(project_id: str, file_path: str):
    docs_dir = Path(get_project(project_id).docs_dir).resolve()
    doc_path = (docs_dir / f"{file_path}.md").resolve()
    if not doc_path.is_relative_to(docs_dir):
        raise HTTPException(status_code=403, detail="Ruta fuera del directorio de documentación del proyecto.")
    if not doc_path.exists():
        raise HTTPException(status_code=404, detail=f"No hay documentación generada para '{file_path}'.")
    draft = verification.draft_path(doc_path)
    return {"file_path": file_path, "content_markdown": doc_path.read_text(encoding="utf-8"),
            "review": verification.doc_status(docs_dir.parent, docs_dir, file_path, _hub_public_key()),
            "draft_markdown": draft.read_text(encoding="utf-8") if draft.exists() else None}


class DocUpdate(BaseModel):
    content: str = Field(..., min_length=1, max_length=200_000)


def _hub_public_key() -> Optional[str]:
    try:
        settings = team.hub_settings()
    except (httpx.HTTPError, KeyError, ValueError):
        return None
    return settings.public_key if settings else None


def _sign(request: Request, project_id: str, kind: str, ref: str, content: str) -> dict:
    url = team.hub_url()
    if not url:
        raise HTTPException(status_code=409, detail="Verificar requiere el modo equipo (Beacon Hub).")
    try:
        res = httpx.post(f"{url}/projects/{project_id}/verifications", json={"kind": kind, "ref": ref, "digest": verification.digest(content)},
                         headers={"Authorization": request.headers.get("authorization", "")}, timeout=_HUB_TIMEOUT)
    except httpx.HTTPError as e:
        raise HTTPException(status_code=503, detail=f"No se pudo contactar al Beacon Hub: {e}")
    if res.status_code != 200:
        raise HTTPException(status_code=res.status_code, detail=res.json().get("detail", "No se pudo firmar la verificación."))
    return res.json()


def _doc_paths(project_id: str, file_path: str):
    docs_dir = Path(get_project(project_id).docs_dir).resolve()
    doc_path = (docs_dir / f"{file_path}.md").resolve()
    if not doc_path.is_relative_to(docs_dir):
        raise HTTPException(status_code=403, detail="Ruta fuera del directorio de documentación del proyecto.")
    return docs_dir, doc_path


@app.put("/projects/{project_id}/docs")
def update_doc(project_id: str, file_path: str, body: DocUpdate, request: Request, verify: bool = True):
    """A tech lead corrects a generated document (by default it is verified at the same time). Accepting the new AI
    draft is the same operation with the draft's text."""
    docs_dir, doc_path = _doc_paths(project_id, file_path)
    if not doc_path.exists():
        raise HTTPException(status_code=404, detail=f"No hay documentación generada para '{file_path}'.")
    doc_path.write_text(body.content, encoding="utf-8")
    verification.draft_path(doc_path).unlink(missing_ok=True)
    if verify:
        signed = _sign(request, project_id, "doc", file_path, body.content)
        verification.record_doc(docs_dir.parent, file_path, signed["token"], signed["reviewer"], corrected=True)
    return {"review": verification.doc_status(docs_dir.parent, docs_dir, file_path, _hub_public_key())}


@app.post("/projects/{project_id}/docs/verify")
def verify_doc(project_id: str, file_path: str, request: Request):
    """A tech lead verifies a document as it is."""
    docs_dir, doc_path = _doc_paths(project_id, file_path)
    if not doc_path.exists():
        raise HTTPException(status_code=404, detail=f"No hay documentación generada para '{file_path}'.")
    signed = _sign(request, project_id, "doc", file_path, doc_path.read_text(encoding="utf-8"))
    verification.record_doc(docs_dir.parent, file_path, signed["token"], signed["reviewer"], corrected=False)
    return {"review": verification.doc_status(docs_dir.parent, docs_dir, file_path, _hub_public_key())}


class AnswerVerify(BaseModel):
    question: str = Field(..., min_length=3, max_length=4000)
    answer: str = Field(..., min_length=1, max_length=40000)
    sources: list = Field(default_factory=list)
    corrected: bool = False


@app.post("/projects/{project_id}/answers/verify")
def verify_answer(project_id: str, body: AnswerVerify, request: Request):
    """A tech lead verifies (and possibly corrects) an answer: it is offered first when someone asks something similar."""
    engine = _get_engine(project_id)
    project = get_project(project_id)
    answer_id = uuid.uuid4().hex[:12]
    signed = _sign(request, project_id, "answer", answer_id, body.answer)
    embedding = embed_query(engine.backend, engine.ai_config.embedding_model, body.question)
    item = verification.add_answer(Path(project.docs_dir).parent, project.repo_path, body.question, body.answer, body.sources,
                                   embedding, signed["token"], signed["reviewer"], body.corrected, answer_id=answer_id)
    return item


@app.get("/projects/{project_id}/answers")
def verified_answers(project_id: str):
    project = get_project(project_id)
    return {"answers": verification.list_answers(Path(project.docs_dir).parent, project.repo_path, _hub_public_key())}


@app.delete("/projects/{project_id}/answers/{answer_id}")
def delete_verified_answer(project_id: str, answer_id: str):
    project = get_project(project_id)
    if not verification.remove_answer(Path(project.docs_dir).parent, answer_id):
        raise HTTPException(status_code=404, detail="Respuesta verificada no encontrada.")
    return {"status": "ok"}


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


@app.get("/system/monitor")
def system_monitor():
    """Result of the periodic check of the model server: whether it answered the last time, since when it fails and the history."""
    return _monitor.state()


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
