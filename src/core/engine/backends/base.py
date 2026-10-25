"""Contract between the engine and the model servers.

The engine only talks to a `Backend`; each server (Ollama today) is an adapter that translates its
protocol and its errors into this contract. Every backend runs models on infrastructure controlled by
the organization.
"""

from dataclasses import dataclass
from typing import Callable, List, Optional, Protocol


class BackendError(Exception):
    pass


class BackendUnavailableError(BackendError):
    """The server cannot be reached."""


class ModelNotFoundError(BackendError):
    """The server answers, but the requested model is not available in it."""

    def __init__(self, model: str, detail: str = ""):
        super().__init__(detail or f"model '{model}' not found")
        self.model = model


class InputRejectedError(BackendError):
    """The server rejected the input, typically because the text does not fit in the model."""


class LLMStoppedError(RuntimeError):
    pass


class LLMTimeoutError(LLMStoppedError):
    pass


class LLMCancelledError(LLMStoppedError):
    pass


@dataclass
class RawChat:
    content: str
    prompt_tokens: int = 0  # tokens the server reports having processed (0 if not reported)
    answer_tokens: int = 0


@dataclass
class BackendHealth:
    reachable: bool
    version: str = ""
    error: str = ""


class Backend(Protocol):
    id: str
    host: str

    def chat(self, model: str, system: str, user: str, *, num_ctx: int, max_tokens: int, temperature: float,
             seed: int, max_seconds: Optional[float] = None,
             should_stop: Optional[Callable[[], bool]] = None) -> RawChat:
        """One chat call. With `max_seconds` or `should_stop` it must be stoppable (LLMStoppedError)."""

    def embed(self, model: str, texts: List[str], *, num_ctx: int) -> List[List[float]]:
        """One vector per text. Texts that do not fit must raise InputRejectedError, never be cut silently."""

    def list_models(self, timeout: Optional[float] = None) -> List[str]:
        """Names of the models available in the server."""

    def health(self, timeout: float = 3) -> BackendHealth: ...

    def install_hint(self, model: str) -> str:
        """How to make a missing model available (shown by `beacon doctor`)."""

    def start_hint(self) -> str:
        """How to start the server when it cannot be reached."""
