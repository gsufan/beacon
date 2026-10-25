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

# provider id -> (display name, factory(host, api_key) -> Backend)
_REGISTRY: Dict[str, tuple] = {
    "ollama": ("Ollama", lambda host, api_key: OllamaBackend(host=host or OLLAMA_DEFAULT_HOST)),
    "openai": ("OpenAI-compatible API", lambda host, api_key: OpenAICompatibleBackend(host or OPENAI_DEFAULT_HOST, api_key)),
}

DEFAULT_HOSTS = {"ollama": OLLAMA_DEFAULT_HOST, "openai": OPENAI_DEFAULT_HOST}


def available_providers() -> List[dict]:
    return [{"id": pid, "name": name} for pid, (name, _) in _REGISTRY.items()]


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
        _, factory = _REGISTRY[provider]
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
