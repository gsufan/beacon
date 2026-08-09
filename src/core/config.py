"""Carga config/config.yaml. Reemplaza al .env: todo lo configurable vive acá."""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import yaml

# Busca config.yaml relativo a la raíz del proyecto, no al cwd del usuario
_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "config.yaml"
CONFIG_PATH = Path(os.getenv("DEUDA_TECNICA_CONFIG", _DEFAULT_CONFIG_PATH))

# Tokens de repos privados: archivo separado, gitignoreado, NUNCA junto a config.yaml
# (config.yaml está versionado en git; un token ahí sería un leak de secreto).
_DEFAULT_CREDENTIALS_PATH = Path(__file__).resolve().parents[2] / "config" / "credentials.yaml"
CREDENTIALS_PATH = Path(os.getenv("DEUDA_TECNICA_CREDENTIALS", _DEFAULT_CREDENTIALS_PATH))


@dataclass
class AIProviderConfig:
    provider: str
    ollama_host: str
    embedding_model: str
    llm_model: str


@dataclass
class ProjectEntry:
    id: str
    name: str
    repo_path: str
    source_type: str = "local"  # "local" | "git"
    repo_url: Optional[str] = None
    auto_watch: bool = False


@dataclass
class AppConfig:
    ai_provider: AIProviderConfig
    projects: List[ProjectEntry]


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
        embedding_model=ai.get("embedding_model", "nomic-embed-text"),
        llm_model=ai.get("llm_model", "llama3:8b"),
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
    provider: str, ollama_host: str, embedding_model: str, llm_model: str, path: Path = CONFIG_PATH
) -> AIProviderConfig:
    raw = _read_raw(path)
    raw["ai_provider"] = {
        "provider": provider, "ollama_host": ollama_host,
        "embedding_model": embedding_model, "llm_model": llm_model,
    }
    _write_raw(raw, path)
    return AIProviderConfig(provider, ollama_host, embedding_model, llm_model)


class ProjectAlreadyExistsError(Exception):
    pass


def _entry_to_raw(entry: ProjectEntry) -> dict:
    return {
        "id": entry.id, "name": entry.name, "repo_path": entry.repo_path,
        "source_type": entry.source_type, "repo_url": entry.repo_url,
        "auto_watch": entry.auto_watch,
    }


def add_project(entry: ProjectEntry, path: Path = CONFIG_PATH) -> ProjectEntry:
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


def set_auto_watch(project_id: str, enabled: bool, path: Path = CONFIG_PATH) -> bool:
    raw = _read_raw(path)
    projects = raw.get("projects", [])
    for p in projects:
        if p.get("id") == project_id:
            p["auto_watch"] = enabled
            _write_raw(raw, path)
            return True
    return False


# ---------- Credenciales (config/credentials.yaml, gitignoreado) ----------

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
