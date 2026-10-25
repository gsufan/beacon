"""Study session: persistent state in a local file and construction of the results file.

The results file contains no source code (only relative paths and line ranges of the sources), user
name, local paths, IP or machine name. The optional identity goes in a separate file.
"""

import hashlib
import json
import os
import secrets
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

SCHEMA_VERSION = 1
PROFILES = ("abierto", "piloto")
CONSENT_KINDS = ("participacion", "hardware", "envio")
CORRECT_VALUES = ("si", "parcial", "no", "no_se")
MIN_QUESTIONS = 3


class StudyError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clean_source(src: dict) -> dict:
    """Only the relative file and line range: never the code or an absolute path of the machine."""
    path = str(src.get("archivo") or src.get("file_path") or "").replace("\\", "/")
    if path.startswith("/") or (len(path) > 1 and path[1] == ":"):
        path = path.rsplit("/", 1)[-1]  # absolute path: keep only the name
    start, end = src.get("start_line"), src.get("end_line")
    lines = src.get("lineas") or (f"{start}-{end}" if start and end else "")
    return {"archivo": path, "lineas": str(lines)}


class StudySession:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.state = self._load()

    def _load(self) -> dict:
        if self.path.exists():
            return json.loads(self.path.read_text(encoding="utf-8"))
        return {}

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(self.state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    @property
    def started(self) -> bool:
        return bool(self.state)

    def start(self, profile: str = "abierto", code: Optional[str] = None, order: int = 1, language: str = "es",
              beacon_version: str = "", beacon_commit: str = ""):
        if profile not in PROFILES:
            raise StudyError(f"perfil inválido: {profile}")
        if profile == "piloto" and not code:
            raise StudyError("el perfil piloto requiere un código de participante")
        self.state = {
            "meta": {"id": secrets.token_hex(3), "perfil": profile, "codigo": code, "orden": int(order), "beacon_version": beacon_version,
                     "beacon_commit": beacon_commit, "idioma": language, "inicio": _now(), "fin": None},
            "consentimientos": [], "hardware": None, "instalacion": None, "repo": None, "preguntas": [],
            "mini_tarea": None, "tareas_piloto": [], "experiencia": None, "identidad": None,
        }
        self._save()

    def _require_started(self):
        if not self.started:
            raise StudyError("la sesión no ha comenzado")

    def consent(self, kind: str, accepted: bool):
        self._require_started()
        if kind not in CONSENT_KINDS:
            raise StudyError(f"tipo de consentimiento inválido: {kind}")
        self.state["consentimientos"].append({"tipo": kind, "aceptado": bool(accepted), "ts": _now()})
        if kind == "participacion" and accepted and not self.state["meta"].get("participacion_desde"):
            # The duration counts from when the person accepts to participate, not from when the program started.
            self.state["meta"]["participacion_desde"] = _now()
        if kind == "hardware" and not accepted:
            self.state["hardware"] = None  # if withdrawn, it is deleted
        self._save()

    def has_consent(self, kind: str) -> bool:
        """The latest consent of that kind wins (it can be withdrawn)."""
        last = [c for c in self.state.get("consentimientos", []) if c["tipo"] == kind]
        return bool(last and last[-1]["aceptado"])

    def set_hardware(self, hardware: dict, ollama_version: str = "", models: Optional[List[str]] = None):
        if not self.has_consent("hardware"):
            raise StudyError("falta el consentimiento para recoger datos del equipo")
        self.state["hardware"] = dict(hardware, ollama_version=ollama_version, modelos=list(models or []))
        self._save()

    def set_installation(self, minutes: Optional[float], problems: str = ""):
        self._require_started()
        self.state["instalacion"] = {"minutos": minutes, "problemas": problems}
        self._save()

    def set_repo(self, url: str, commit: str, files: int, chunks: int, languages: List[str], indexing_seconds: float):
        self._require_started()
        self.state["repo"] = {"url": url, "commit": commit, "archivos_indexados": int(files), "fragmentos": int(chunks),
                              "lenguajes": list(languages), "indexado_segundos": round(float(indexing_seconds), 1)}
        self._save()

    def add_question(self, question: str, answer: str, seconds: float, prompt_tokens: int = 0, answer_tokens: int = 0,
                     sources: Optional[List[dict]] = None) -> int:
        self._require_started()
        n = len(self.state["preguntas"]) + 1
        self.state["preguntas"].append({
            "n": n, "ts": _now(), "pregunta": question, "respuesta": answer, "segundos_respuesta": round(float(seconds), 2),
            "tokens_prompt": int(prompt_tokens), "tokens_respuesta": int(answer_tokens),
            "fuentes": [_clean_source(s) for s in (sources or [])], "valoracion": None})
        self._save()
        return n

    def rate(self, n: int, correct: str, useful_sources: Optional[int] = None, solved_alone: Optional[bool] = None,
             comment: str = ""):
        if correct not in CORRECT_VALUES:
            raise StudyError(f"valoración inválida: {correct}")
        if useful_sources is not None and not 1 <= int(useful_sources) <= 5:
            raise StudyError("la utilidad de las fuentes va de 1 a 5")
        try:
            question = self.state["preguntas"][n - 1]
        except (IndexError, KeyError):
            raise StudyError(f"no existe la pregunta {n}")
        question["valoracion"] = {"correcta": correct, "fuentes_utiles": useful_sources, "resolvio_sola": solved_alone,
                                  "comentario": comment}
        self._save()

    def set_mini_task(self, description: str, seconds_without: Optional[float], seconds_with: Optional[float], matches: str):
        self._require_started()
        self.state["mini_tarea"] = {"descripcion": description, "segundos_sin_beacon": seconds_without,
                                    "segundos_con_beacon": seconds_with, "coincide": matches}
        self._save()

    def add_pilot_task(self, task_id: str, condition: str, seconds: float, capped: bool, tools: str, explanation: str):
        self._require_started()
        if condition not in ("con", "sin"):
            raise StudyError("la condición es 'con' o 'sin'")
        self.state["tareas_piloto"].append({"id": task_id, "condicion": condition, "segundos": round(float(seconds), 1),
                                            "tope": bool(capped), "herramientas_declaradas": tools, "explicacion": explanation})
        self._save()

    def set_experience(self, experience: dict):
        self._require_started()
        self.state["experiencia"] = experience
        self._save()

    def set_identity(self, github: str = "", presentation: str = ""):
        self._require_started()
        self.state["identidad"] = {"github": github, "presentacion": presentation} if (github or presentation) else None
        self._save()

    def can_finish(self) -> bool:
        return self.has_consent("participacion") and len(self.state.get("preguntas", [])) >= MIN_QUESTIONS

    def build_result(self) -> dict:
        """Results file (without identity). Requires participation consent and the minimum number of questions."""
        self._require_started()
        if not self.can_finish():
            raise StudyError(f"hacen falta el consentimiento de participación y al menos {MIN_QUESTIONS} preguntas")
        s = self.state
        meta = dict(s["meta"], fin=_now())
        if meta.get("participacion_desde"):
            meta["inicio"] = meta.pop("participacion_desde")
        questions = s["preguntas"]
        digest = hashlib.sha256(json.dumps([q["pregunta"] for q in questions], ensure_ascii=False).encode("utf-8")).hexdigest()
        return {"esquema": SCHEMA_VERSION, "meta": meta, "consentimientos": s["consentimientos"], "hardware": s["hardware"],
                "instalacion": s["instalacion"], "repo": s["repo"], "preguntas": questions, "mini_tarea": s["mini_tarea"],
                "tareas_piloto": s["tareas_piloto"], "experiencia": s["experiencia"],
                "integridad": {"sha256_de_preguntas": digest}}

    def build_identity(self) -> Optional[dict]:
        """Carries the same short id as the results file: it is the only link between them."""
        identity = self.state.get("identidad")
        return dict(identity, id=self.state["meta"]["id"]) if identity else None

    def discard(self):
        if self.path.exists():
            self.path.unlink()
        self.state = {}
