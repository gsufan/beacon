"""Loading and validation of config/config.yaml and the credentials."""

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import yaml

# Looked up relative to the project root, not the user's cwd
_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "config.yaml"
CONFIG_PATH = Path(os.getenv("DEUDA_TECNICA_CONFIG", _DEFAULT_CONFIG_PATH))

# Tokens for private repositories: a separate file, kept out of git and never in config.yaml, which the API
# exposes in GET /config.
_DEFAULT_CREDENTIALS_PATH = Path(__file__).resolve().parents[2] / "config" / "credentials.yaml"
CREDENTIALS_PATH = Path(os.getenv("DEUDA_TECNICA_CREDENTIALS", _DEFAULT_CREDENTIALS_PATH))

DEFAULT_EMBEDDING_MODEL = "qwen3-embedding:0.6b"


# Default context window: the maximum of llama3:8b (see core/engine/llm.py).
DEFAULT_LLM_NUM_CTX = 8192
MIN_LLM_NUM_CTX = 2048


@dataclass
class AIProviderConfig:
    provider: str
    ollama_host: str
    embedding_model: str
    llm_model: str
    llm_num_ctx: int = DEFAULT_LLM_NUM_CTX


@dataclass
class ProjectEntry:
    id: str
    name: str
    repo_path: str
    source_type: str = "local"
    repo_url: Optional[str] = None
    auto_watch: bool = False


@dataclass
class AppConfig:
    ai_provider: AIProviderConfig
    projects: List[ProjectEntry]


def _valid_num_ctx(value) -> int:
    """Context window from config.yaml; the default if missing or not a reasonable integer."""
    if isinstance(value, int) and not isinstance(value, bool) and value >= MIN_LLM_NUM_CTX:
        return value
    return DEFAULT_LLM_NUM_CTX


def load_config(path: Path = CONFIG_PATH) -> AppConfig:
    if not path.exists():
        raise FileNotFoundError(
            f"No se encontró config.yaml en {path}. "
            f"Copia config/config.example.yaml a config/config.yaml y ajústalo."
        )
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))

    ai = raw.get("ai_provider", {})
    provider = AIProviderConfig(
        provider=ai.get("provider", "ollama"),
        ollama_host=ai.get("ollama_host", "http://localhost:11434"),
        embedding_model=ai.get("embedding_model", DEFAULT_EMBEDDING_MODEL),
        llm_model=ai.get("llm_model", "llama3:8b"),
        llm_num_ctx=_valid_num_ctx(ai.get("llm_num_ctx")),
    )

    projects = [
        ProjectEntry(
            id=p["id"], name=p.get("name", p["id"]), repo_path=p["repo_path"],
            source_type=p.get("source_type", "local"), repo_url=p.get("repo_url"),
            auto_watch=p.get("auto_watch", False),
        )
        for p in raw.get("projects", [])
    ]

    return AppConfig(ai_provider=provider, projects=projects)


def _read_raw(path: Path = CONFIG_PATH) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"No se encontró config.yaml en {path}.")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _write_raw(raw: dict, path: Path = CONFIG_PATH):
    path.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")


def update_ai_provider(
    provider: str, ollama_host: str, embedding_model: str, llm_model: str,
    path: Path = CONFIG_PATH, llm_num_ctx: Optional[int] = None,
) -> AIProviderConfig:
    """`llm_num_ctx=None` keeps the window already configured."""
    raw = _read_raw(path)
    if llm_num_ctx is None:
        llm_num_ctx = _valid_num_ctx(raw.get("ai_provider", {}).get("llm_num_ctx"))
    llm_num_ctx = _valid_num_ctx(llm_num_ctx)
    ai = {
        "provider": provider, "ollama_host": ollama_host,
        "embedding_model": embedding_model, "llm_model": llm_model,
    }
    if llm_num_ctx != DEFAULT_LLM_NUM_CTX:
        ai["llm_num_ctx"] = llm_num_ctx  # only written when it differs from the default
    raw["ai_provider"] = ai
    _write_raw(raw, path)
    return AIProviderConfig(provider, ollama_host, embedding_model, llm_model, llm_num_ctx)


class ProjectAlreadyExistsError(Exception):
    pass


class InvalidProjectIdError(ValueError):
    pass


# The id is a folder name (data/<id>/): it must reject paths such as "..".
_PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def validate_project_id(project_id: str):
    if not _PROJECT_ID_RE.match(project_id or ""):
        raise InvalidProjectIdError(
            f"Id de proyecto inválido: '{project_id}'. Usa letras, números, '.', '_' o '-' "
            "(máx. 64 caracteres, empezando con letra o número)."
        )


def _entry_to_raw(entry: ProjectEntry) -> dict:
    return {
        "id": entry.id, "name": entry.name, "repo_path": entry.repo_path,
        "source_type": entry.source_type, "repo_url": entry.repo_url,
        "auto_watch": entry.auto_watch,
    }


def add_project(entry: ProjectEntry, path: Path = CONFIG_PATH) -> ProjectEntry:
    validate_project_id(entry.id)
    raw = _read_raw(path)
    projects = raw.setdefault("projects", [])
    if any(p.get("id") == entry.id for p in projects):
        raise ProjectAlreadyExistsError(f"El proyecto '{entry.id}' ya está registrado.")
    projects.append(_entry_to_raw(entry))
    _write_raw(raw, path)
    return entry


def remove_project(project_id: str, path: Path = CONFIG_PATH) -> bool:
    raw = _read_raw(path)
    projects = raw.get("projects", [])
    remaining = [p for p in projects if p.get("id") != project_id]
    if len(remaining) == len(projects):
        return False
    raw["projects"] = remaining
    _write_raw(raw, path)
    return True


def update_project(
    project_id: str, name: Optional[str] = None, repo_path: Optional[str] = None,
    path: Path = CONFIG_PATH,
) -> bool:
    """Updates individual fields of a registered project. None means "leave that field"; use set_auto_watch to change auto_watch."""
    raw = _read_raw(path)
    projects = raw.get("projects", [])
    for p in projects:
        if p.get("id") == project_id:
            if name is not None:
                p["name"] = name
            if repo_path is not None:
                p["repo_path"] = repo_path
            _write_raw(raw, path)
            return True
    return False


def set_auto_watch(project_id: str, enabled: bool, path: Path = CONFIG_PATH) -> bool:
    raw = _read_raw(path)
    projects = raw.get("projects", [])
    for p in projects:
        if p.get("id") == project_id:
            p["auto_watch"] = enabled
            _write_raw(raw, path)
            return True
    return False



def get_project_token(project_id: str, path: Path = CREDENTIALS_PATH) -> Optional[str]:
    if not path.exists():
        return None
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return raw.get("projects", {}).get(project_id)


def set_project_token(project_id: str, token: str, path: Path = CREDENTIALS_PATH):
    raw = {}
    if path.exists():
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw.setdefault("projects", {})[project_id] = token
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")


def delete_project_token(project_id: str, path: Path = CREDENTIALS_PATH):
    if not path.exists():
        return
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw.get("projects", {}).pop(project_id, None)
    path.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
