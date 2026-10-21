"""Endpoints del modo estudio (`/study/*`). Solo se montan cuando el servidor se levantó con `beacon study`
(variable BEACON_STUDY=1): un servidor normal no los expone.

El participante aprueba cada paso; los tiempos de las preguntas los mide el servidor, no el navegador."""

import json
import logging
import shutil
import threading
import time
from pathlib import Path
from typing import Callable, Optional

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from core import services
from core.config import load_config
from core.engine.indexer import IndexModelMismatchError
from core.projects import DATA_ROOT, InvalidRepoUrlError, ProjectNotFoundError
from core.study import hardware
from core.study.session import CORRECT_VALUES, StudyError, StudySession

logger = logging.getLogger("beacon.study")

STUDY_PROJECT_ID = "estudio"
MAX_FILES = 3000  # más que esto son horas de indexado: se pide un repositorio más chico
ALLOWED_HOSTS = ("github.com", "gitlab.com", "bitbucket.org", "codeberg.org")


def study_dir() -> Path:
    return DATA_ROOT / "_study"


def output_dir() -> Path:
    """Raíz del proyecto: ahí quedan los archivos finales, para que el participante los encuentre y los revise."""
    return Path(__file__).resolve().parents[3]


def result_file(study_id: str) -> str:
    return f"beacon-estudio-resultados-{study_id}.json"


def identity_file(study_id: str) -> str:
    return f"beacon-estudio-identidad-{study_id}.json"


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


class IdentityBody(BaseModel):
    github: str = Field(default="", max_length=300)
    presentacion: str = Field(default="", max_length=3000)


def check_public_repo_url(url: str) -> str:
    """Solo https y servidores de código conocidos: el estudio se hace con repositorios públicos."""
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

    def session() -> StudySession:
        return StudySession(session_path())

    def need_started() -> StudySession:
        s = session()
        if not s.started:
            raise HTTPException(status_code=409, detail="La sesión del estudio no ha comenzado.")
        return s

    def fail(e: StudyError):
        raise HTTPException(status_code=400, detail=str(e))

    # ---------- sesión ----------
    @router.get("/state")
    def state():
        s = session()
        if not s.started:
            return {"started": False}
        st = s.state
        return {"started": True, "meta": st["meta"], "consentimientos": {k: s.has_consent(k) for k in ("participacion", "hardware", "envio")},
                "hardware": st["hardware"], "repo": st["repo"], "repo_status": status["repo"],
                "preguntas": [{"n": q["n"], "pregunta": q["pregunta"], "respuesta": q["respuesta"], "segundos": q["segundos_respuesta"],
                               "fuentes": q["fuentes"], "valoracion": q["valoracion"]} for q in st["preguntas"]],
                "puede_terminar": s.can_finish(), "experiencia": st["experiencia"], "identidad": st["identidad"]}

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

    # ---------- datos del equipo ----------
    @router.get("/hardware-preview")
    def hardware_preview():
        """Lo que se guardaría. No se guarda nada hasta que el participante lo autoriza."""
        return hardware.collect_hardware(str(DATA_ROOT))

    @router.post("/hardware")
    def hardware_save():
        s = need_started()
        models, version = [], ""
        try:
            ai = load_config().ai_provider
            import ollama
            models = [m["model"] for m in ollama.Client(host=ai.ollama_host, timeout=3).list().get("models", [])]
            version = httpx.get(ai.ollama_host.rstrip("/") + "/api/version", timeout=3).json().get("version", "")
        except Exception:
            pass
        try:
            s.set_hardware(hardware.collect_hardware(str(DATA_ROOT)), ollama_version=version, models=models)
        except StudyError as e:
            fail(e)
        return s.state["hardware"]

    # ---------- repositorio ----------
    def _prepare_repo(url: str):
        started = time.time()
        try:
            try:
                clear_engines()
                services.unregister_project(STUDY_PROJECT_ID, purge_data=True)
            except (ProjectNotFoundError, services.InvalidRequestError):
                pass
            repo_dir = DATA_ROOT / STUDY_PROJECT_ID
            if repo_dir.exists():
                shutil.rmtree(repo_dir, ignore_errors=True)
            services.register_project(STUDY_PROJECT_ID, name="Repositorio del estudio", repo_url=url)
            from core.projects import get_project
            from core.engine.chunker import detect_language
            repo_path = Path(get_project(STUDY_PROJECT_ID).repo_path)
            files = [p for p in repo_path.rglob("*") if p.is_file() and ".git" not in p.parts and detect_language(str(p))]
            if len(files) > MAX_FILES:
                services.unregister_project(STUDY_PROJECT_ID, purge_data=True)
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

            result = services.sync_project(STUDY_PROJECT_ID, docs=False, pull=False, on_index_progress=on_progress)
            clear_engines()
            engine = get_engine(STUDY_PROJECT_ID)
            import git
            commit = git.Repo(repo_path).head.commit.hexsha[:10]
            langs = sorted({detect_language(str(p)) for p in files})
            session().set_repo(url, commit, result.index.get("archivos_indexados", len(files)), engine.collection.count(),
                               langs, time.time() - started)
            with lock:
                status["repo"] = {"estado": "listo", "detalle": None}
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
            if status["repo"]["estado"] in ("clonando", "indexando"):
                raise HTTPException(status_code=409, detail="Ya se está preparando un repositorio.")
            status["repo"] = {"estado": "clonando", "detalle": url}
        threading.Thread(target=_prepare_repo, args=(url,), daemon=True).start()
        return {"estado": "clonando"}

    @router.get("/repo-status")
    def repo_status():
        with lock:
            return dict(status["repo"])

    # ---------- preguntas ----------
    @router.post("/ask")
    def ask(body: AskBody):
        s = need_started()
        if not s.has_consent("participacion"):
            raise HTTPException(status_code=409, detail="Falta el consentimiento de participación.")
        if s.state["repo"] is None:
            raise HTTPException(status_code=409, detail="Todavía no hay un repositorio listo.")
        try:
            engine = get_engine(STUDY_PROJECT_ID)
            t0 = time.perf_counter()
            result = engine.ask(body.pregunta, language=body.idioma)
            seconds = time.perf_counter() - t0
        except IndexModelMismatchError as e:
            raise HTTPException(status_code=409, detail=str(e))
        except (ConnectionError, httpx.TransportError):
            raise HTTPException(status_code=503, detail="No se pudo conectar a Ollama. Verifica que esté ejecutándose.")
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"Error en el motor: {e}")
        data = result.to_dict()
        n = s.add_question(body.pregunta, data["answer"], seconds, result.prompt_tokens, result.answer_tokens, data["sources"])
        return {"n": n, "respuesta": data["answer"], "segundos": round(seconds, 2), "fuentes": data["sources"]}

    @router.post("/rate")
    def rate(body: RateBody):
        try:
            need_started().rate(body.n, body.correcta, body.fuentes_utiles, body.resolvio_sola, body.comentario)
        except StudyError as e:
            fail(e)
        return {"ok": True}

    # ---------- experiencia, identidad y cierre ----------
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
        """Archivo para revisar y descargar. Exige el consentimiento de envío y el mínimo de preguntas."""
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

    @router.post("/discard")
    def discard():
        session().discard()
        with lock:
            status["repo"] = {"estado": "sin_repo", "detalle": None}
        try:
            clear_engines()
            services.unregister_project(STUDY_PROJECT_ID, purge_data=True)
        except Exception:  # noqa: BLE001
            pass
        return {"ok": True}

    return router
