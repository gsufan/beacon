"""Loading and validation of config/config.yaml and the credentials."""

import os
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import List, Optional

import yaml

from core.engine.backends import DEFAULT_HOSTS, create_backend

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
class CloudSettings:
    """What Beacon requires before using a server outside the local network (set by the user, no defaults)."""
    consent: bool = False
    consent_date: str = ""
    budget_usd: float = 0.0
    price_input_per_million: float = 0.0
    price_output_per_million: float = 0.0
    price_embedding_per_million: float = 0.0


@dataclass
class AIProviderConfig:
    provider: str
    host: str
    embedding_model: str
    llm_model: str
    llm_num_ctx: int = DEFAULT_LLM_NUM_CTX
    cloud: Optional[CloudSettings] = None
    api_key: str = field(default="", repr=False)  # read from credentials.yaml; never serialized

    def public_dict(self) -> dict:
        d = {"provider": self.provider, "host": self.host, "embedding_model": self.embedding_model,
             "llm_model": self.llm_model, "llm_num_ctx": self.llm_num_ctx}
        if self.cloud is not None:
            d["cloud"] = dict(self.cloud.__dict__)
        return d

    def backend(self, data_dir=None):
        return create_backend(self.provider, self.host, api_key=self.api_key, cloud=self.cloud, data_dir=data_dir)


@dataclass
class ProjectEntry:
    id: str
    name: str
    repo_path: str
    source_type: str = "local"
    repo_url: Optional[str] = None
    auto_watch: bool = False
    ai: Optional[dict] = None  # optional override of ai_provider for this project (no secrets)


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
        host=ai.get("host") or ai.get("ollama_host") or DEFAULT_HOSTS.get(ai.get("provider", "ollama"), ""),  # `ollama_host`: legacy key
        embedding_model=ai.get("embedding_model", DEFAULT_EMBEDDING_MODEL),
        llm_model=ai.get("llm_model", "llama3:8b"),
        llm_num_ctx=_valid_num_ctx(ai.get("llm_num_ctx")),
    )

    projects = [
        ProjectEntry(
            id=p["id"], name=p.get("name", p["id"]), repo_path=p["repo_path"],
            source_type=p.get("source_type", "local"), repo_url=p.get("repo_url"),
            auto_watch=p.get("auto_watch", False), ai=p.get("ai"),
        )
        for p in raw.get("projects", [])
    ]

    return AppConfig(ai_provider=provider, projects=projects)


def _cloud_from(raw: Optional[dict]) -> Optional[CloudSettings]:
    if not isinstance(raw, dict):
        return None
    def num(key):
        v = raw.get(key, 0)
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 else 0.0
    return CloudSettings(consent=raw.get("consent") is True, consent_date=str(raw.get("consent_date", "")), budget_usd=num("budget_usd"),
                         price_input_per_million=num("price_input_per_million"), price_output_per_million=num("price_output_per_million"),
                         price_embedding_per_million=num("price_embedding_per_million"))


def resolve_ai_config(project_id: Optional[str] = None, cfg: Optional["AppConfig"] = None, credentials_path: Optional[Path] = None) -> AIProviderConfig:
    """The AI configuration of a project: the global one, overridden by the project's own `ai` section if it has one."""
    cfg = cfg or load_config()
    base = cfg.ai_provider
    entry = next((p for p in cfg.projects if p.id == project_id), None)
    if entry is None or not entry.ai:
        return base
    ai = entry.ai
    provider = ai.get("provider", base.provider)
    host = ai.get("host") or (base.host if provider == base.provider else DEFAULT_HOSTS.get(provider, ""))
    return AIProviderConfig(
        provider=provider, host=host,
        embedding_model=ai.get("embedding_model", base.embedding_model), llm_model=ai.get("llm_model", base.llm_model),
        llm_num_ctx=_valid_num_ctx(ai.get("llm_num_ctx")) if "llm_num_ctx" in ai else base.llm_num_ctx,
        cloud=_cloud_from(ai.get("cloud")), api_key=get_api_key(project_id, credentials_path or CREDENTIALS_PATH) or "",
    )


def set_project_ai(project_id: str, ai: Optional[dict], path: Path = CONFIG_PATH) -> bool:
    """Sets (or with None removes) the AI override of a project."""
    raw = _read_raw(path)
    for p in raw.get("projects", []):
        if p.get("id") == project_id:
            if ai:
                p["ai"] = ai
            else:
                p.pop("ai", None)
            _write_raw(raw, path)
            return True
    return False


def consent_today() -> str:
    return date.today().isoformat()


def _read_raw(path: Path = CONFIG_PATH) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"No se encontró config.yaml en {path}.")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _write_raw(raw: dict, path: Path = CONFIG_PATH):
    path.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")


def update_ai_provider(
    provider: str, host: str, embedding_model: str, llm_model: str,
    path: Path = CONFIG_PATH, llm_num_ctx: Optional[int] = None,
) -> AIProviderConfig:
    """`llm_num_ctx=None` keeps the window already configured."""
    raw = _read_raw(path)
    if llm_num_ctx is None:
        llm_num_ctx = _valid_num_ctx(raw.get("ai_provider", {}).get("llm_num_ctx"))
    llm_num_ctx = _valid_num_ctx(llm_num_ctx)
    ai = {
        "provider": provider, "host": host,
        "embedding_model": embedding_model, "llm_model": llm_model,
    }
    if llm_num_ctx != DEFAULT_LLM_NUM_CTX:
        ai["llm_num_ctx"] = llm_num_ctx  # only written when it differs from the default
    raw["ai_provider"] = ai
    _write_raw(raw, path)
    return AIProviderConfig(provider, host, embedding_model, llm_model, llm_num_ctx)


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


def get_api_key(project_id: Optional[str], path: Path = CREDENTIALS_PATH) -> Optional[str]:
    """API key of the project's external model server (credentials.yaml, `api_keys`, or BEACON_API_KEY_<ID> in the environment)."""
    if project_id is None:
        return None
    env = os.getenv("BEACON_API_KEY_" + re.sub(r"[^A-Za-z0-9]", "_", project_id).upper())
    if env:
        return env
    if not path.exists():
        return None
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return (raw.get("api_keys") or {}).get(project_id)


def set_api_key(project_id: str, key: Optional[str], path: Path = CREDENTIALS_PATH):
    raw = {}
    if path.exists():
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    keys = raw.setdefault("api_keys", {})
    if key:
        keys[project_id] = key
    else:
        keys.pop(project_id, None)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")


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
