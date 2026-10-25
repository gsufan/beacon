"""Ollama adapter."""

import time
from typing import Callable, List, Optional

import httpx
import ollama

from core.engine.backends.base import (
    BackendHealth,
    BackendUnavailableError,
    InputRejectedError,
    LLMCancelledError,
    LLMTimeoutError,
    ModelNotFoundError,
    RawChat,
)

DEFAULT_HOST = "http://localhost:11434"


class OllamaBackend:
    id = "ollama"

    def __init__(self, client=None, host: str = DEFAULT_HOST):
        self.host = host
        self._client = client  # injectable for tests; otherwise created per call so timeouts can differ

    def _get_client(self, timeout: Optional[float] = None):
        if self._client is not None:
            return self._client
        return ollama.Client(host=self.host, timeout=timeout) if timeout else ollama.Client(host=self.host)

    def _translate(self, error: Exception, model: str = ""):
        if isinstance(error, ollama.ResponseError):
            if error.status_code == 404:
                return ModelNotFoundError(model, str(error))
            return InputRejectedError(str(error))
        if isinstance(error, (ConnectionError, httpx.TransportError)):
            return BackendUnavailableError(f"cannot connect to Ollama at '{self.host}': {error}")
        return None

    def chat(self, model, system, user, *, num_ctx, max_tokens, temperature, seed, max_seconds=None, should_stop=None) -> RawChat:
        kwargs = dict(
            model=model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            options={"num_ctx": num_ctx, "temperature": temperature, "seed": seed, "num_predict": max_tokens},
        )
        try:
            if max_seconds is not None or should_stop is not None:
                deadline = time.monotonic() + max_seconds if max_seconds is not None else None
                response = self._stream(deadline, should_stop, **kwargs)
            else:
                response = self._get_client().chat(**kwargs)
        except (ollama.ResponseError, ConnectionError, httpx.TransportError) as e:
            raise self._translate(e, model) from e
        return RawChat(response["message"]["content"], response.get("prompt_eval_count") or 0, response.get("eval_count") or 0)

    def _stream(self, deadline: Optional[float], should_stop: Optional[Callable[[], bool]], **kwargs) -> dict:
        """Streaming chat so it can be stopped: between chunks it checks the time limit and cancellation,
        and closing the stream drops the Ollama connection."""
        parts, last = [], {}
        stream = self._get_client().chat(stream=True, **kwargs)
        try:
            for chunk in stream:
                parts.append((chunk.get("message") or {}).get("content", ""))
                if chunk.get("done"):
                    last = chunk
                if should_stop is not None and should_stop():
                    raise LLMCancelledError("La respuesta se canceló.")
                if deadline is not None and time.monotonic() > deadline:
                    raise LLMTimeoutError("La respuesta tardó más del tiempo máximo y se detuvo.")
        finally:
            close = getattr(stream, "close", None)
            if close:
                close()
        return {"message": {"content": "".join(parts)}, "prompt_eval_count": last.get("prompt_eval_count"),
                "eval_count": last.get("eval_count")}

    def embed(self, model: str, texts: List[str], *, num_ctx: int) -> List[List[float]]:
        # truncate=False: if a text does not fit, Ollama returns an error (instead of silently cutting it)
        try:
            response = self._get_client().embed(model=model, input=texts, truncate=False, options={"num_ctx": num_ctx})
        except (ollama.ResponseError, ConnectionError, httpx.TransportError) as e:
            raise self._translate(e, model) from e
        vectors = response["embeddings"]
        if len(vectors) != len(texts) or any(not v for v in vectors):
            raise InputRejectedError("the model returned empty embeddings")
        return vectors

    def list_models(self, timeout: Optional[float] = None) -> List[str]:
        try:
            return [m["model"] for m in self._get_client(timeout).list().get("models", [])]
        except (ollama.ResponseError, ConnectionError, httpx.TransportError) as e:
            raise self._translate(e) from e

    def health(self, timeout: float = 3) -> BackendHealth:
        try:
            version = httpx.get(self.host.rstrip("/") + "/api/version", timeout=timeout).json().get("version", "")
            return BackendHealth(True, version)
        except Exception as e:  # noqa: BLE001
            return BackendHealth(False, error=str(e))

    def install_hint(self, model: str) -> str:
        return f"ollama pull {model}"

    def start_hint(self) -> str:
        return "ollama serve"
