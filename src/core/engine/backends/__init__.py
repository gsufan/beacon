"""Model server backends and their registry."""

from typing import Dict, List

from core.engine.backends.base import (
    Backend,
    BackendError,
    BackendHealth,
    BackendUnavailableError,
    InputRejectedError,
    LLMCancelledError,
    LLMStoppedError,
    LLMTimeoutError,
    ModelNotFoundError,
    RawChat,
)
from core.engine.backends.ollama_backend import DEFAULT_HOST as OLLAMA_DEFAULT_HOST
from core.engine.backends.ollama_backend import OllamaBackend

# provider id -> (display name, factory(host) -> Backend)
_REGISTRY: Dict[str, tuple] = {
    "ollama": ("Ollama", lambda host: OllamaBackend(host=host or OLLAMA_DEFAULT_HOST)),
}

DEFAULT_HOSTS = {"ollama": OLLAMA_DEFAULT_HOST}


def available_providers() -> List[dict]:
    return [{"id": pid, "name": name} for pid, (name, _) in _REGISTRY.items()]


def create_backend(provider: str, host: str) -> Backend:
    try:
        _, factory = _REGISTRY[provider]
    except KeyError:
        raise BackendError(f"unknown provider '{provider}' (available: {', '.join(_REGISTRY)})") from None
    return factory(host)


__all__ = [
    "Backend", "BackendError", "BackendHealth", "BackendUnavailableError", "InputRejectedError", "LLMCancelledError",
    "LLMStoppedError", "LLMTimeoutError", "ModelNotFoundError", "OllamaBackend", "RawChat", "DEFAULT_HOSTS",
    "available_providers", "create_backend",
]
