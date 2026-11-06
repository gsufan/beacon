"""Loading and validation of config/config.yaml and the credentials."""

import copy
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, List, Optional

import yaml

from core.engine.backends import DEFAULT_HOSTS, create_backend
from core.project_lock import file_lock

# Looked up relative to the project root, not the user's cwd
_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "config.yaml"
CONFIG_PATH = Path(os.getenv("DEUDA_TECNICA_CONFIG", _DEFAULT_CONFIG_PATH))

# Tokens for private repositories: a separate file, kept out of git and never in config.yaml, which the API
# exposes in GET /config.
_DEFAULT_CREDENTIALS_PATH = Path(__file__).resolve().parents[2] / "config" / "credentials.yaml"
CREDENTIALS_PATH = Path(os.getenv("DEUDA_TECNICA_CREDENTIALS", _DEFAULT_CREDENTIALS_PATH))

DEFAULT_PROVIDER = "ollama"
DEFAULT_EMBEDDING_MODEL = "qwen3-embedding:0.6b"
DEFAULT_LLM_MODEL = "llama3:8b"

# Default context window: the maximum of llama3:8b (see core/engine/llm.py).
DEFAULT_LLM_NUM_CTX = 8192
MIN_LLM_NUM_CTX = 2048


@dataclass
class AIProviderConfig:
    provider: str
    host: str
    embedding_model: str
    llm_model: str
    llm_num_ctx: int = DEFAULT_LLM_NUM_CTX

    def public_dict(self) -> dict:
        return dict(self.__dict__)

    def backend(self):
        return create_backend(self.provider, self.host)


def default_ai_provider() -> AIProviderConfig:
    return AIProviderConfig(DEFAULT_PROVIDER, DEFAULT_HOSTS[DEFAULT_PROVIDER], DEFAULT_EMBEDDING_MODEL, DEFAULT_LLM_MODEL)


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

    def find_project(self, project_id: str) -> Optional[ProjectEntry]:
        return next((p for p in self.projects if p.id == project_id), None)


class ProjectAlreadyExistsError(Exception):
    pass


class InvalidProjectIdError(ValueError):
    pass


def _valid_num_ctx(value) -> int:
    """Context window from config.yaml; the default if missing or not a reasonable integer."""
    if isinstance(value, int) and not isinstance(value, bool) and value >= MIN_LLM_NUM_CTX:
        return value
    return DEFAULT_LLM_NUM_CTX


def _read_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _write_yaml(raw: dict, path: Path):
    """Atomic: another process never reads a half-written file, and a crash leaves the previous one intact."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
    os.replace(tmp, path)


@contextmanager
def _editing(path: Path, missing_ok: bool = False) -> Iterator[dict]:
    """Read-modify-write of a YAML file under a cross-process lock, so concurrent changes (server, watcher,
    console) are not lost. The file is only rewritten if the yielded dict was changed.
    """
    with file_lock(path.with_name(f"{path.name}.lock")):
        if path.exists():
            raw = _read_yaml(path)
        elif missing_ok:
            raw = {}
        else:
            raise FileNotFoundError(f"No se encontró config.yaml en {path}.")
        before = copy.deepcopy(raw)
        yield raw
        if raw != before:
            _write_yaml(raw, path)


def load_config(path: Path = CONFIG_PATH) -> AppConfig:
    if not path.exists():
        raise FileNotFoundError(
            f"No se encontró config.yaml en {path}. "
            f"Copia config/config.example.yaml a config/config.yaml y ajústalo."
        )
    raw = _read_yaml(path)

    ai = raw.get("ai_provider", {})
    provider_id = ai.get("provider", DEFAULT_PROVIDER)
    provider = AIProviderConfig(
        provider=provider_id,
        host=ai.get("host") or ai.get("ollama_host") or DEFAULT_HOSTS.get(provider_id, ""),  # `ollama_host`: legacy key
        embedding_model=ai.get("embedding_model", DEFAULT_EMBEDDING_MODEL),
        llm_model=ai.get("llm_model", DEFAULT_LLM_MODEL),
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


def update_ai_provider(
    provider: str, host: str, embedding_model: str, llm_model: str,
    path: Path = CONFIG_PATH, llm_num_ctx: Optional[int] = None,
) -> AIProviderConfig:
    """`llm_num_ctx=None` keeps the window already configured."""
    with _editing(path) as raw:
        if llm_num_ctx is None:
            llm_num_ctx = raw.get("ai_provider", {}).get("llm_num_ctx")
        llm_num_ctx = _valid_num_ctx(llm_num_ctx)
        ai = {
            "provider": provider, "host": host,
            "embedding_model": embedding_model, "llm_model": llm_model,
        }
        if llm_num_ctx != DEFAULT_LLM_NUM_CTX:
            ai["llm_num_ctx"] = llm_num_ctx  # only written when it differs from the default
        raw["ai_provider"] = ai
    return AIProviderConfig(provider, host, embedding_model, llm_model, llm_num_ctx)


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
    with _editing(path) as raw:
        projects = raw.setdefault("projects", [])
        if any(p.get("id") == entry.id for p in projects):
            raise ProjectAlreadyExistsError(f"El proyecto '{entry.id}' ya está registrado.")
        projects.append(_entry_to_raw(entry))
    return entry


def remove_project(project_id: str, path: Path = CONFIG_PATH) -> bool:
    with _editing(path) as raw:
        projects = raw.get("projects", [])
        remaining = [p for p in projects if p.get("id") != project_id]
        if len(remaining) == len(projects):
            return False
        raw["projects"] = remaining
    return True


def _update_fields(project_id: str, fields: dict, path: Path) -> bool:
    with _editing(path) as raw:
        for p in raw.get("projects", []):
            if p.get("id") == project_id:
                p.update(fields)
                return True
    return False


def update_project(
    project_id: str, name: Optional[str] = None, repo_path: Optional[str] = None,
    path: Path = CONFIG_PATH,
) -> bool:
    """Updates individual fields of a registered project. None means "leave that field"; use set_auto_watch to change auto_watch."""
    fields = {k: v for k, v in (("name", name), ("repo_path", repo_path)) if v is not None}
    return _update_fields(project_id, fields, path)


def set_auto_watch(project_id: str, enabled: bool, path: Path = CONFIG_PATH) -> bool:
    return _update_fields(project_id, {"auto_watch": enabled}, path)


def get_project_token(project_id: str, path: Path = CREDENTIALS_PATH) -> Optional[str]:
    if not path.exists():
        return None
    return _read_yaml(path).get("projects", {}).get(project_id)


def set_project_token(project_id: str, token: str, path: Path = CREDENTIALS_PATH):
    with _editing(path, missing_ok=True) as raw:
        raw.setdefault("projects", {})[project_id] = token


def delete_project_token(project_id: str, path: Path = CREDENTIALS_PATH):
    if not path.exists():
        return
    with _editing(path) as raw:
        raw.get("projects", {}).pop(project_id, None)
