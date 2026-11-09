"""Synthetic check of the model server, run by the API server on a schedule.

/healthz only says the Beacon process is up. This check also proves that the model server answers and can run
both models (a short embedding and a one-token answer), so an outage is detected and logged even on a day
without users. The result is kept in memory and served by GET /system/monitor.
"""

import logging
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Callable, Optional

from core.config import AIProviderConfig
from core.engine.backends import create_backend, model_available
from core.engine.embeddings import embed_query
from core.engine.llm import chat

logger = logging.getLogger("beacon.monitor")

CHECK_TIMEOUT_SECONDS = 60
HISTORY_SIZE = 50


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ModelMonitor:
    """`ai_config` returns the current configuration (it is read on every check, so a change of server applies)."""

    def __init__(self, ai_config: Callable[[], AIProviderConfig], backend_factory=create_backend):
        self.ai_config = ai_config
        self.backend_factory = backend_factory
        self._lock = threading.Lock()
        self.history: deque = deque(maxlen=HISTORY_SIZE)
        self.last_ok: Optional[str] = None
        self.consecutive_failures = 0

    def _probe(self, ai: AIProviderConfig) -> None:
        backend = self.backend_factory(ai.provider, ai.host)
        installed = backend.list_models(timeout=10)
        missing = [m for m in (ai.embedding_model, ai.llm_model) if not model_available(m, installed)]
        if missing:
            raise RuntimeError(f"modelos no disponibles: {', '.join(missing)}")
        embed_query(backend, ai.embedding_model, "ok")
        chat(backend, ai.llm_model, "", "ok", ai.llm_num_ctx, max_tokens=1, max_seconds=CHECK_TIMEOUT_SECONDS)

    def check(self) -> dict:
        started = time.perf_counter()
        try:
            ai = self.ai_config()
            self._probe(ai)
            error = ""
        except Exception as e:  # noqa: BLE001 (any failure counts as the service being down)
            error = str(e) or e.__class__.__name__
        result = {"time": _now(), "ok": not error, "seconds": round(time.perf_counter() - started, 2), "error": error}
        with self._lock:
            was_failing = self.consecutive_failures > 0
            self.history.append(result)
            if result["ok"]:
                self.last_ok = result["time"]
                self.consecutive_failures = 0
            else:
                self.consecutive_failures += 1
        if not result["ok"]:
            logger.warning("monitor: el servidor de modelos no responde o no puede ejecutar los modelos (%s)", error)
        elif was_failing:
            logger.warning("monitor: el servidor de modelos se recuperó")
        return result

    def state(self) -> dict:
        with self._lock:
            history = list(self.history)
            return {"ok": bool(history) and history[-1]["ok"], "last_check": history[-1] if history else None,
                    "last_ok": self.last_ok, "consecutive_failures": self.consecutive_failures, "history": history}

    def run(self, stop: threading.Event, interval_seconds: float):
        """Checks right away and then every `interval_seconds` until `stop` is set."""
        while True:
            self.check()
            if stop.wait(interval_seconds):
                return
