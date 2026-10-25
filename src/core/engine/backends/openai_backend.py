"""Adapter for servers with an OpenAI-compatible API (`/chat/completions`, `/embeddings`, `/models`).

It covers OpenAI itself, aggregators such as OpenRouter and local servers such as llama.cpp, vLLM or LM Studio.
It uses plain HTTP (httpx) so no vendor SDK is needed.
"""

import json
import time
from typing import Callable, List, Optional

import httpx

from core.engine.backends.base import (
    BackendAuthError,
    BackendError,
    BackendHealth,
    BackendRateLimitError,
    BackendUnavailableError,
    InputRejectedError,
    LLMCancelledError,
    LLMTimeoutError,
    ModelNotFoundError,
    RawChat,
)

DEFAULT_HOST = "https://api.openai.com/v1"
EMBED_BATCH = 100
MAX_ATTEMPTS = 4
MAX_WAIT_SECONDS = 20

# Parameters some models reject; when the server complains about one, it is adapted and the call is repeated.
_REJECTABLE = ("temperature", "seed")


class OpenAICompatibleBackend:
    id = "openai"

    def __init__(self, host: str = DEFAULT_HOST, api_key: str = "", transport=None, sleep: Callable[[float], None] = time.sleep):
        self.host = host.rstrip("/")
        self._api_key = api_key
        self._transport = transport  # injectable for tests
        self._sleep = sleep
        self._drop: set = set()  # parameters this server rejected
        self._completion_tokens_key = "max_tokens"
        self.last_embedding_tokens = 0

    # ---- HTTP ----------------------------------------------------------------------------------------------

    def _client(self, timeout: Optional[float]) -> httpx.Client:
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        return httpx.Client(base_url=self.host, headers=headers, timeout=timeout, transport=self._transport)

    @staticmethod
    def _message(response: httpx.Response) -> str:
        try:
            error = response.json().get("error", {})
            return error.get("message") or str(error) or response.text[:200]
        except ValueError:
            return response.text[:200]

    def _raise_for(self, response: httpx.Response, model: str = ""):
        status, message = response.status_code, self._message(response)
        lowered = message.lower()
        if status in (401, 403):
            raise BackendAuthError(f"the server rejected the API key or its access to '{model or 'this resource'}': {message}")
        if status == 404 or "model" in lowered and ("does not exist" in lowered or "not found" in lowered):
            raise ModelNotFoundError(model, message)
        if status == 400 and ("context" in lowered and ("length" in lowered or "window" in lowered) or "too long" in lowered
                              or "maximum context" in lowered or "reduce the length" in lowered):
            raise InputRejectedError(message)
        raise BackendError(f"HTTP {status}: {message}")

    def _send(self, method: str, path: str, *, model: str = "", timeout: Optional[float] = 120, **kwargs) -> httpx.Response:
        """One request with retries for rate limits and transient server errors."""
        delay = 1.0
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                with self._client(timeout) as client:
                    response = client.request(method, path, **kwargs)
            except (httpx.TransportError, ConnectionError) as e:
                raise BackendUnavailableError(f"cannot connect to '{self.host}': {e}") from e
            if response.status_code == 429 or response.status_code >= 500:
                if attempt == MAX_ATTEMPTS:
                    if response.status_code == 429:
                        raise BackendRateLimitError(f"the server is rate limiting requests: {self._message(response)}")
                    raise BackendUnavailableError(f"the server failed with HTTP {response.status_code}: {self._message(response)}")
                try:
                    wait = float(response.headers.get("retry-after", delay))
                except ValueError:
                    wait = delay
                self._sleep(min(wait, MAX_WAIT_SECONDS))
                delay *= 2
                continue
            if response.status_code >= 400:
                self._raise_for(response, model)
            return response
        raise BackendError("unreachable")  # pragma: no cover

    # ---- chat ----------------------------------------------------------------------------------------------

    def _payload(self, model, system, user, max_tokens, temperature, seed, stream) -> dict:
        payload = {"model": model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                   self._completion_tokens_key: max_tokens, "temperature": temperature, "seed": seed}
        for key in self._drop:
            payload.pop(key, None)
        if stream:
            payload["stream"] = True
            payload["stream_options"] = {"include_usage": True}
        return payload

    def _adapt(self, error: BackendError) -> bool:
        """Adjusts the parameters after a 400 that names one the model does not accept. True if it changed something."""
        text = str(error).lower()
        if "max_completion_tokens" in text and self._completion_tokens_key == "max_tokens":
            self._completion_tokens_key = "max_completion_tokens"
            return True
        for key in _REJECTABLE:
            if key in text and key not in self._drop:
                self._drop.add(key)
                return True
        return False

    def chat(self, model, system, user, *, num_ctx, max_tokens, temperature, seed, max_seconds=None, should_stop=None) -> RawChat:
        stoppable = max_seconds is not None or should_stop is not None
        deadline = time.monotonic() + max_seconds if max_seconds is not None else None
        for _ in range(4):
            payload = self._payload(model, system, user, max_tokens, temperature, seed, stoppable)
            try:
                return self._stream(payload, model, deadline, should_stop) if stoppable else self._complete(payload, model)
            except BackendError as e:
                if type(e) is BackendError and self._adapt(e):
                    continue
                raise
        raise BackendError("the server kept rejecting the request parameters")

    def _complete(self, payload: dict, model: str) -> RawChat:
        data = self._send("POST", "/chat/completions", model=model, json=payload).json()
        usage = data.get("usage") or {}
        return RawChat(data["choices"][0]["message"].get("content") or "", usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))

    def _stream(self, payload: dict, model: str, deadline: Optional[float], should_stop) -> RawChat:
        parts, usage = [], {}
        try:
            with self._client(None) as client, client.stream("POST", "/chat/completions", json=payload) as response:
                if response.status_code >= 400:
                    response.read()
                    self._raise_for(response, model)
                for line in response.iter_lines():
                    if line.startswith("data:"):
                        body = line[5:].strip()
                        if body == "[DONE]":
                            break
                        chunk = json.loads(body)
                        usage = chunk.get("usage") or usage
                        for choice in chunk.get("choices") or []:
                            parts.append((choice.get("delta") or {}).get("content") or "")
                    if should_stop is not None and should_stop():
                        raise LLMCancelledError("La respuesta se canceló.")
                    if deadline is not None and time.monotonic() > deadline:
                        raise LLMTimeoutError("La respuesta tardó más del tiempo máximo y se detuvo.")
        except (httpx.TransportError, ConnectionError) as e:
            raise BackendUnavailableError(f"cannot connect to '{self.host}': {e}") from e
        return RawChat("".join(parts), usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))

    # ---- embeddings, models, health -------------------------------------------------------------------------

    def embed(self, model: str, texts: List[str], *, num_ctx: int) -> List[List[float]]:
        vectors, tokens = [], 0
        for i in range(0, len(texts), EMBED_BATCH):
            batch = texts[i:i + EMBED_BATCH]
            data = self._send("POST", "/embeddings", model=model, json={"model": model, "input": batch}).json()
            items = sorted(data["data"], key=lambda d: d.get("index", 0))
            if len(items) != len(batch) or any(not d.get("embedding") for d in items):
                raise InputRejectedError("the server returned empty embeddings")
            vectors += [d["embedding"] for d in items]
            tokens += (data.get("usage") or {}).get("total_tokens", 0)
        self.last_embedding_tokens = tokens
        return vectors

    def list_models(self, timeout: Optional[float] = None) -> List[str]:
        data = self._send("GET", "/models", timeout=timeout or 30).json()
        return [m["id"] for m in data.get("data", [])]

    def health(self, timeout: float = 3) -> BackendHealth:
        try:
            self._send("GET", "/models", timeout=timeout)
            return BackendHealth(True)
        except BackendError as e:
            return BackendHealth(False, error=str(e))

    def install_hint(self, model: str) -> str:
        return f"enable the model '{model}' in your account or server"

    def start_hint(self) -> str:
        return "check the host and the API key"
