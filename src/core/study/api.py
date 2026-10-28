"""Study mode endpoints (`/study/*`). They are only mounted when the server was started with `beacon study`
(BEACON_STUDY=1): a regular server does not expose them. The participant approves each step; question
timings are measured by the server, not the browser.
"""

import json
import logging
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Callable, Optional

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from core import services
from core.config import load_config
from core.engine.backends import BackendUnavailableError, create_backend
from core.engine.indexer import IndexModelMismatchError
from core.engine.llm import LLMCancelledError, LLMTimeoutError, chat
from core.fs import remove_tree
from core.projects import DATA_ROOT, InvalidRepoUrlError, ProjectNotFoundError
from core.study import hardware, submit
from core.study.session import CORRECT_VALUES, StudyError, StudySession
from core.study.validate import validate_result

logger = logging.getLogger("beacon.study")

STUDY_PROJECT_ID = "estudio"
ASK_TIMEOUT_SECONDS = 180  # an answer that takes longer is usually a model loop: it is cut with a notice
MAX_FILES = 3000  # more than this is hours of indexing: a smaller repository is requested
ALLOWED_HOSTS = ("github.com", "gitlab.com", "bitbucket.org", "codeberg.org")


def study_dir() -> Path:
    return DATA_ROOT / "_study"


def output_dir() -> Path:
    """Project root: the final files are left there so the participant can find and review them."""
    return Path(__file__).resolve().parents[3]


def result_file(study_id: str) -> str:
    return f"beacon-estudio-resultados-{study_id}.json"


def identity_file(study_id: str) -> str:
    return f"beacon-estudio-identidad-{study_id}.json"


def study_project_ids() -> list:
    """Ids of the study repositories registered in its own configuration (the most recent last)."""
    import yaml
    try:
        raw = yaml.safe_load((study_dir() / "config.yaml").read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return []
    return [p["id"] for p in raw.get("projects") or [] if str(p.get("id", "")).startswith(STUDY_PROJECT_ID)]


def current_project_id() -> str:
    ids = study_project_ids()
    return ids[-1] if ids else STUDY_PROJECT_ID


def remove_study_repos(keep: Optional[str] = None):
    """Deletes the study repositories (all but `keep`). Whatever another process or Windows has open is left for later: it stays registered and the next cleanup retries."""
    for project_id in study_project_ids():
        if project_id == keep:
            continue
        try:
            services.unregister_project(project_id, purge_data=True)
        except Exception:  # noqa: BLE001
            pass
        remove_tree(DATA_ROOT / project_id, ignore_errors=True)


def session_path() -> Path:
    return study_dir() / "sesion.json"


class StartBody(BaseModel):
    perfil: str = "abierto"
    codigo: Optional[str] = None
    orden: int = 1
    idioma: str = "es"


class ConsentBody(BaseModel):
    tipo: str
    aceptado: bool


class RepoBody(BaseModel):
    url: str = Field(..., min_length=10, max_length=300)


class AskBody(BaseModel):
    pregunta: str = Field(..., min_length=3, max_length=2000)
    idioma: str = "es"


class RateBody(BaseModel):
    n: int
    correcta: str
    fuentes_utiles: Optional[int] = None
    resolvio_sola: Optional[bool] = None
    comentario: str = Field(default="", max_length=2000)


class ExperienceBody(BaseModel):
    datos: dict


class SubmitBody(BaseModel):
    con_identidad: bool = False


class IdentityBody(BaseModel):
    github: str = Field(default="", max_length=300)
    presentacion: str = Field(default="", max_length=3000)


def run_health_checks() -> list:
    """Pre-study checks, so it does not fail midway. Each item has an id (the screen translates it), a status ("ok", "warn" or "error") and a value to display."""
    items = []
    try:
        ai = load_config().ai_provider
        provider, host, embedding, llm = ai.provider, ai.host, ai.embedding_model, ai.llm_model
    except FileNotFoundError:
        provider, host, embedding, llm = "ollama", "http://localhost:11434", "qwen3-embedding:0.6b", "llama3:8b"

    installed, ollama_ok = [], False
    try:
        installed = create_backend(provider, host).list_models(timeout=4)
        ollama_ok = True
    except Exception:  # noqa: BLE001
        pass
    items.append({"id": "ollama", "estado": "ok" if ollama_ok else "error", "valor": host})
    for item_id, model in (("model_embedding", embedding), ("model_llm", llm)):
        present = any(model == m or f"{model}:latest" == m for m in installed)
        items.append({"id": item_id, "estado": "ok" if present else ("error" if ollama_ok else "skip"), "valor": model})

    snapshot = hardware.collect_hardware(str(DATA_ROOT))
    gpus = snapshot.get("gpus") or []
    if not gpus:
        items.append({"id": "gpu", "estado": "warn", "valor": ""})
    else:
        vram = max(g["vram_gb"] for g in gpus)
        items.append({"id": "gpu", "estado": "ok" if vram >= 6 else "warn", "valor": f"{gpus[0]['modelo']} ({vram:g} GB)"})
    free = snapshot.get("disco_libre_gb")
    items.append({"id": "disk", "estado": "ok" if free is None or free >= 2 else ("warn" if free >= 0.5 else "error"),
                  "valor": "" if free is None else f"{free:g} GB"})
    try:
        httpx.head("https://github.com", timeout=5, follow_redirects=True)
        items.append({"id": "network", "estado": "ok", "valor": "github.com"})
    except Exception:  # noqa: BLE001
        items.append({"id": "network", "estado": "error", "valor": "github.com"})
    return items


def warm_up_model(engine):
    """Loads the answer model into memory so the first question is not timed with the load (several seconds on a cold start)."""
    try:
        chat(engine.backend, engine.ai_config.llm_model, "", "ok", engine.ai_config.llm_num_ctx, max_tokens=1, max_seconds=60)
    except Exception:  # noqa: BLE001
        logger.info("no se pudo precargar el modelo de respuestas; la primera pregunta puede tardar más")


def check_public_repo_url(url: str) -> str:
    """Only https and known code hosts: the study uses public repositories."""
    from urllib.parse import urlparse
    parsed = urlparse(url.strip())
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in ALLOWED_HOSTS:
        raise InvalidRepoUrlError("Usa la dirección https de un repositorio público de GitHub, GitLab, Bitbucket o Codeberg.")
    if len([p for p in parsed.path.split("/") if p]) < 2:
        raise InvalidRepoUrlError("La dirección debe incluir el usuario y el repositorio (por ejemplo https://github.com/usuario/repo).")
    return url.strip()


def build_router(get_engine: Callable, clear_engines: Callable) -> APIRouter:
    router = APIRouter(prefix="/study")
    status = {"repo": {"estado": "sin_repo", "detalle": None}}
    lock = threading.Lock()
    cancel = threading.Event()  # stops the answer in progress ("Cancel" button or server shutdown)

    def session() -> StudySession:
        return StudySession(session_path())

    def need_started() -> StudySession:
        s = session()
        if not s.started:
            raise HTTPException(status_code=409, detail="La sesión del estudio no ha comenzado.")
        return s

    def fail(e: StudyError):
        raise HTTPException(status_code=400, detail=str(e))

    @router.get("/health")
    def health():
        return {"items": run_health_checks()}

    @router.get("/state")
    def state():
        s = session()
        if not s.started:
            return {"started": False}
        st = s.state
        return {"started": True, "carpeta_repo": str(DATA_ROOT / current_project_id()), "meta": st["meta"], "consentimientos": {k: s.has_consent(k) for k in ("participacion", "hardware", "envio")},
                "hardware": st["hardware"], "repo": st["repo"], "repo_status": status["repo"],
                "preguntas": [{"n": q["n"], "pregunta": q["pregunta"], "respuesta": q["respuesta"], "segundos": q["segundos_respuesta"],
                               "fuentes": q["fuentes"], "valoracion": q["valoracion"]} for q in st["preguntas"]],
                "puede_terminar": s.can_finish(), "experiencia": st["experiencia"], "identidad": st["identidad"],
                "envio": {"disponible": bool(submit.submit_url()), "destino": submit.destination_host(), "enviado": st.get("envio")}}

    @router.post("/start")
    def start(body: StartBody):
        s = session()
        try:
            s.start(profile=body.perfil, code=body.codigo, order=body.orden, language=body.idioma, beacon_version="0.3.0",
                    beacon_commit=hardware.beacon_commit())
        except StudyError as e:
            fail(e)
        return {"started": True}

    @router.post("/consent")
    def consent(body: ConsentBody):
        try:
            need_started().consent(body.tipo, body.aceptado)
        except StudyError as e:
            fail(e)
        return {"ok": True}

    @router.get("/hardware-preview")
    def hardware_preview():
        """What would be saved. Nothing is stored until the participant authorizes it."""
        return hardware.collect_hardware(str(DATA_ROOT))

    @router.post("/hardware")
    def hardware_save():
        s = need_started()
        models, version = [], ""
        try:
            ai = load_config().ai_provider
            backend = create_backend(ai.provider, ai.host)
            models = backend.list_models(timeout=3)
            version = backend.health().version
        except Exception:
            pass
        try:
            s.set_hardware(hardware.collect_hardware(str(DATA_ROOT)), ollama_version=version, models=models)
        except StudyError as e:
            fail(e)
        return s.state["hardware"]

    def _prepare_repo(url: str):
        started = time.time()
        # Each repository goes to a new folder: replacing the previous one in place fails on Windows if the index (or an
        # answer in progress) has it open. The previous one is deleted at the end, whether or not that succeeds.
        project_id = f"{STUDY_PROJECT_ID}-{secrets.token_hex(2)}"
        try:
            clear_engines()
            services.register_project(project_id, name="Repositorio del estudio", repo_url=url)
            from core.projects import get_project
            from core.engine.chunker import detect_language
            repo_path = Path(get_project(project_id).repo_path)
            files = [p for p in repo_path.rglob("*") if p.is_file() and ".git" not in p.parts and detect_language(str(p))]
            if len(files) > MAX_FILES:
                services.unregister_project(project_id, purge_data=True)
                with lock:
                    status["repo"] = {"estado": "error", "detalle": f"El repositorio tiene {len(files)} archivos de código (el máximo es {MAX_FILES}). Elige uno más chico."}
                return
            with lock:
                status["repo"] = {"estado": "indexando", "detalle": f"{len(files)} archivos de código",
                                  "progreso": {"actual": 0, "total": len(files), "archivo": "", "segundos": 0}}

            def on_progress(i, total, file_path):
                with lock:
                    status["repo"] = {"estado": "indexando", "detalle": f"{len(files)} archivos de código",
                                      "progreso": {"actual": i, "total": total, "archivo": str(file_path),
                                                   "segundos": round(time.time() - started)}}

            result = services.sync_project(project_id, docs=False, pull=False, on_index_progress=on_progress)
            clear_engines()
            engine = get_engine(project_id)
            with lock:
                status["repo"] = {"estado": "calentando", "detalle": None}
            warm_up_model(engine)
            import git
            commit = git.Repo(repo_path).head.commit.hexsha[:10]
            langs = sorted({detect_language(str(p)) for p in files})
            session().set_repo(url, commit, result.index.get("archivos_indexados", len(files)), engine.collection.count(),
                               langs, time.time() - started)
            with lock:
                status["repo"] = {"estado": "listo", "detalle": None}
            clear_engines()
            remove_study_repos(keep=project_id)
        except services.CloneError:
            with lock:
                status["repo"] = {"estado": "error", "detalle": "No se pudo clonar el repositorio. ¿Es público y la dirección es correcta?"}
        except Exception as e:  # noqa: BLE001
            logger.exception("falló la preparación del repositorio del estudio")
            with lock:
                status["repo"] = {"estado": "error", "detalle": f"No se pudo preparar el repositorio: {e}"}

    @router.post("/repo", status_code=202)
    def set_repo(body: RepoBody):
        if need_started().state["preguntas"]:
            raise HTTPException(status_code=409, detail="Ya hay preguntas hechas: para cambiar de repositorio hay que descartar la sesión.")
        try:
            url = check_public_repo_url(body.url)
        except InvalidRepoUrlError as e:
            raise HTTPException(status_code=400, detail=str(e))
        with lock:
            if status["repo"]["estado"] in ("clonando", "indexando", "calentando"):
                raise HTTPException(status_code=409, detail="Ya se está preparando un repositorio.")
            status["repo"] = {"estado": "clonando", "detalle": url}
        threading.Thread(target=_prepare_repo, args=(url,), daemon=True).start()
        return {"estado": "clonando"}

    @router.get("/repo-status")
    def repo_status():
        with lock:
            return dict(status["repo"])

    @router.post("/ask")
    def ask(body: AskBody):
        s = need_started()
        if not s.has_consent("participacion"):
            raise HTTPException(status_code=409, detail="Falta el consentimiento de participación.")
        if s.state["repo"] is None:
            raise HTTPException(status_code=409, detail="Todavía no hay un repositorio listo.")
        try:
            engine = get_engine(current_project_id())
            cancel.clear()
            t0 = time.perf_counter()
            result = engine.ask(body.pregunta, language=body.idioma, max_seconds=ASK_TIMEOUT_SECONDS, should_stop=cancel.is_set)
            seconds = time.perf_counter() - t0
        except LLMCancelledError:
            raise HTTPException(status_code=409, detail="Pregunta cancelada.")
        except LLMTimeoutError:
            raise HTTPException(status_code=504, detail=f"El modelo tardó más de {ASK_TIMEOUT_SECONDS // 60} minutos y se detuvo. "
                                                        "Prueba con una pregunta más concreta.")
        except IndexModelMismatchError as e:
            raise HTTPException(status_code=409, detail=str(e))
        except (BackendUnavailableError, ConnectionError, httpx.TransportError):
            raise HTTPException(status_code=503, detail="No se pudo conectar al servidor de modelos. Verifica que esté ejecutándose.")
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"Error en el motor: {e}")
        data = result.to_dict()
        n = s.add_question(body.pregunta, data["answer"], seconds, result.prompt_tokens, result.answer_tokens, data["sources"])
        return {"n": n, "respuesta": data["answer"], "segundos": round(seconds, 2), "fuentes": data["sources"]}

    @router.post("/cancel")
    def cancel_question():
        cancel.set()
        return {"ok": True}

    @router.post("/rate")
    def rate(body: RateBody):
        try:
            need_started().rate(body.n, body.correcta, body.fuentes_utiles, body.resolvio_sola, body.comentario)
        except StudyError as e:
            fail(e)
        return {"ok": True}

    @router.post("/experience")
    def experience(body: ExperienceBody):
        need_started().set_experience(body.datos)
        return {"ok": True}

    @router.post("/identity")
    def identity(body: IdentityBody):
        need_started().set_identity(body.github, body.presentacion)
        return {"ok": True}

    @router.get("/result")
    def result():
        """File to review and download. Requires the submission consent and the minimum number of questions."""
        s = need_started()
        if not s.has_consent("envio"):
            raise HTTPException(status_code=409, detail="Falta autorizar la generación del archivo.")
        try:
            result, identity = s.build_result(), s.build_identity()
        except StudyError as e:
            raise HTTPException(status_code=409, detail=str(e))
        out = output_dir()
        study_id = result["meta"]["id"]
        (out / result_file(study_id)).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        files = [result_file(study_id)]
        if identity:
            (out / identity_file(study_id)).write_text(json.dumps(identity, ensure_ascii=False, indent=2), encoding="utf-8")
            files.append(identity_file(study_id))
        return {"resultado": result, "identidad": identity, "archivos": files, "carpeta": str(out)}

    @router.post("/submit")
    def submit_results(body: SubmitBody):
        """Sends the reviewed results file (and, if asked, the identity file) to the collector. Only the participant triggers it."""
        s = need_started()
        if not s.has_consent("envio"):
            raise HTTPException(status_code=409, detail="Falta autorizar la generación del archivo.")
        if not submit.submit_url():
            raise HTTPException(status_code=409, detail="Este estudio no tiene un destino de envío configurado.")
        try:
            result = s.build_result()
        except StudyError as e:
            raise HTTPException(status_code=409, detail=str(e))
        problems, _ = validate_result(result)
        if problems:
            raise HTTPException(status_code=409, detail="El archivo no es válido: " + "; ".join(problems))
        identity = s.build_identity() if body.con_identidad else None
        try:
            receipt = submit.send(submit.build_payload(result, identity))
        except submit.SubmitError as e:
            raise HTTPException(status_code=502, detail=str(e))
        s.mark_sent(receipt, identity is not None)
        return {"ok": True, "recibo": receipt or result["meta"]["id"]}

    def _discard_all():
        session().discard()
        with lock:
            status["repo"] = {"estado": "sin_repo", "detalle": None}
        clear_engines()
        remove_study_repos()
        remove_tree(DATA_ROOT / STUDY_PROJECT_ID, ignore_errors=True)  # the clone of the previous version, if left behind

    @router.post("/discard")
    def discard():
        _discard_all()
        return {"ok": True}

    @router.post("/quit")
    def quit_study():
        """Discards everything and stops the study server (the console that launched it notices and exits)."""
        _discard_all()
        threading.Timer(0.7, lambda: os._exit(0)).start()  # gives the response time to reach the browser
        return {"ok": True}

    router.shutdown = cancel.set  # called by the server on shutdown, so an answer in progress does not hang it
    return router
