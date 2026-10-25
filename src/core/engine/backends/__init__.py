"""Model server backends and their registry."""

from typing import Dict, List

from core.engine.backends.base import (
    Backend,
    BackendError,
    BackendHealth,
    BackendAuthError,
    BackendRateLimitError,
    BackendUnavailableError,
    ExternalServerError,
    InputRejectedError,
    LLMCancelledError,
    LLMStoppedError,
    LLMTimeoutError,
    ModelNotFoundError,
    RawChat,
)
from core.engine.backends.ollama_backend import DEFAULT_HOST as OLLAMA_DEFAULT_HOST
from core.engine.backends.locality import is_local_host
from core.engine.backends.ollama_backend import OllamaBackend
from core.engine.backends.openai_backend import DEFAULT_HOST as OPENAI_DEFAULT_HOST
from core.engine.backends.openai_backend import OpenAICompatibleBackend

def _openai_like(provider_id: str, default_host: str, model_hint: str, start_hint: str):
    return lambda host, api_key: OpenAICompatibleBackend(host or default_host, api_key, provider_id=provider_id, model_hint=model_hint, start_hint=start_hint)


# provider id -> (display name, default host, factory(host, api_key) -> Backend). Local servers first.
_REGISTRY: Dict[str, tuple] = {
    "ollama": ("Ollama", OLLAMA_DEFAULT_HOST, lambda host, api_key: OllamaBackend(host=host or OLLAMA_DEFAULT_HOST)),
    "lmstudio": ("LM Studio", "http://localhost:1234/v1",
                 _openai_like("lmstudio", "http://localhost:1234/v1", "download and load the model '{model}' in LM Studio",
                              "start the local server in LM Studio (Developer tab)")),
    "llamacpp": ("llama.cpp server", "http://localhost:8080/v1",
                 _openai_like("llamacpp", "http://localhost:8080/v1", "start llama-server with the model '{model}' (embeddings need --embeddings)",
                              "start llama-server")),
    "vllm": ("vLLM", "http://localhost:8000/v1",
             _openai_like("vllm", "http://localhost:8000/v1", "serve the model '{model}' with vLLM", "start the vLLM server")),
    "openai": ("OpenAI-compatible server (other)", OPENAI_DEFAULT_HOST, _openai_like("openai", OPENAI_DEFAULT_HOST, "", "")),
}

DEFAULT_HOSTS = {pid: default for pid, (_, default, _f) in _REGISTRY.items()}


def available_providers() -> List[dict]:
    return [{"id": pid, "name": name, "default_host": host} for pid, (name, host, _) in _REGISTRY.items()]


def create_backend(provider: str, host: str) -> Backend:
    """The backend for a provider. Only servers on this machine or on a private network are supported."""
    try:
        _, _, factory = _REGISTRY[provider]
    except KeyError:
        raise BackendError(f"unknown provider '{provider}' (available: {', '.join(_REGISTRY)})") from None
    host = host or DEFAULT_HOSTS.get(provider, "")
    if not is_local_host(host):
        raise ExternalServerError(
            f"'{host}' is outside this machine and the local network. Beacon only runs models on infrastructure the "
            "organization controls; servers outside it are only in the experimental branch `experimental/paid-apis`.")
    return factory(host, "")


__all__ = [
    "BackendAuthError", "BackendRateLimitError", "ExternalServerError", "OpenAICompatibleBackend", "is_local_host",
    "Backend", "BackendError", "BackendHealth", "BackendUnavailableError", "InputRejectedError", "LLMCancelledError",
    "LLMStoppedError", "LLMTimeoutError", "ModelNotFoundError", "OllamaBackend", "RawChat", "DEFAULT_HOSTS",
    "available_providers", "create_backend",
]
