"""Model server backends and their registry."""

from typing import Dict, List

from core.engine.backends.base import (
    Backend,
    BackendError,
    BackendHealth,
    BackendAuthError,
    BackendRateLimitError,
    BackendUnavailableError,
    BudgetExceededError,
    CloudNotConfiguredError,
    InputRejectedError,
    LLMCancelledError,
    LLMStoppedError,
    LLMTimeoutError,
    ModelNotFoundError,
    RawChat,
)
from core.engine.backends.ollama_backend import DEFAULT_HOST as OLLAMA_DEFAULT_HOST
from core.engine.backends.metering import MeteredBackend, UsageLedger, is_local_host
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
    "openai": ("OpenAI-compatible API (other)", OPENAI_DEFAULT_HOST, _openai_like("openai", OPENAI_DEFAULT_HOST, "", "")),
}

DEFAULT_HOSTS = {pid: default for pid, (_, default, _f) in _REGISTRY.items()}


def available_providers() -> List[dict]:
    return [{"id": pid, "name": name, "default_host": host} for pid, (name, host, _) in _REGISTRY.items()]


def external_problems(host: str, api_key: str, cloud) -> List[str]:
    """What is missing before Beacon may talk to a server outside this machine or the local network."""
    if is_local_host(host):
        return []
    missing = []
    if cloud is None or not getattr(cloud, "consent", False):
        missing.append("explicit consent (the code leaves the organization)")
    if cloud is None or getattr(cloud, "budget_usd", 0) <= 0:
        missing.append("a spending limit in dollars")
    if cloud is None or min(getattr(cloud, "price_input_per_million", 0), getattr(cloud, "price_output_per_million", 0),
                            getattr(cloud, "price_embedding_per_million", 0)) <= 0:
        missing.append("the provider's prices per million tokens (input, output and embeddings)")
    if not api_key:
        missing.append("an API key")
    if not host.lower().startswith("https://"):
        missing.append("an https:// address")
    return missing


def create_backend(provider: str, host: str, *, api_key: str = "", cloud=None, data_dir=None) -> Backend:
    """The backend for a provider. A server outside the local network additionally requires consent, a spending
    limit, prices and an API key (see external_problems); its calls are metered in `data_dir`."""
    try:
        _, _, factory = _REGISTRY[provider]
    except KeyError:
        raise BackendError(f"unknown provider '{provider}' (available: {', '.join(_REGISTRY)})") from None
    host = host or DEFAULT_HOSTS.get(provider, "")
    problems = external_problems(host, api_key, cloud)
    if problems:
        raise CloudNotConfiguredError(
            f"'{host}' is outside the local network and requires: {'; '.join(problems)}. "
            "Set it up with `beacon cloud enable <project>`.")
    backend = factory(host, api_key)
    if not is_local_host(host) and data_dir is not None:
        backend = MeteredBackend(backend, UsageLedger(data_dir), cloud.budget_usd, cloud.price_input_per_million,
                                 cloud.price_output_per_million, cloud.price_embedding_per_million)
    return backend


__all__ = [
    "BackendAuthError", "BackendRateLimitError", "BudgetExceededError", "CloudNotConfiguredError", "MeteredBackend", "OpenAICompatibleBackend",
    "UsageLedger", "external_problems", "is_local_host",
    "Backend", "BackendError", "BackendHealth", "BackendUnavailableError", "InputRejectedError", "LLMCancelledError",
    "LLMStoppedError", "LLMTimeoutError", "ModelNotFoundError", "OllamaBackend", "RawChat", "DEFAULT_HOSTS",
    "available_providers", "create_backend",
]
