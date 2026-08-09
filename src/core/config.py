"""Carga config/config.yaml. Reemplaza al .env: todo lo configurable vive acá."""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import List

import yaml

# Busca config.yaml relativo a la raíz del proyecto, no al cwd del usuario
_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "config.yaml"
CONFIG_PATH = Path(os.getenv("DEUDA_TECNICA_CONFIG", _DEFAULT_CONFIG_PATH))


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
        ProjectEntry(id=p["id"], name=p.get("name", p["id"]), repo_path=p["repo_path"])
        for p in raw.get("projects", [])
    ]

    return AppConfig(ai_provider=provider, projects=projects)
