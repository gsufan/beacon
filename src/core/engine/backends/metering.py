"""Spending control for external servers: a per-project ledger and a wrapper that enforces the limit."""

import ipaddress
import json
import math
import os
import threading
from pathlib import Path
from typing import Callable, List, Optional
from urllib.parse import urlparse

from core.engine.backends.base import Backend, BudgetExceededError, RawChat

CHARS_PER_TOKEN = 3.5  # conservative: it over-estimates, so the limit errs on the safe side
LEDGER_FILE = "usage.json"
_LOCK = threading.Lock()


def is_local_host(host: str) -> bool:
    """True for servers on this machine or on a private network: loopback, private addresses, `.local` names and
    single-label names (such as a Docker Compose service name). Everything else is external."""
    name = (urlparse(host if "://" in host else "http://" + host).hostname or "").lower()
    if not name:
        return False
    if name in ("localhost", "host.docker.internal") or name.endswith((".localhost", ".local", ".internal", ".lan")) or "." not in name:
        return True
    try:
        ip = ipaddress.ip_address(name)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private or ip.is_link_local


def estimate_tokens(chars: int) -> int:
    return math.ceil(chars / CHARS_PER_TOKEN)


class UsageLedger:
    """Tokens and spending of a project, kept in data/<id>/usage.json."""

    def __init__(self, data_dir: Path):
        self.path = Path(data_dir) / LEDGER_FILE

    def read(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"spent_usd": 0.0, "input_tokens": 0, "output_tokens": 0, "embedding_tokens": 0, "calls": 0}

    def add(self, input_tokens=0, output_tokens=0, embedding_tokens=0, cost_usd=0.0):
        with _LOCK:
            data = self.read()
            data["spent_usd"] = round(data["spent_usd"] + cost_usd, 8)
            data["input_tokens"] += input_tokens
            data["output_tokens"] += output_tokens
            data["embedding_tokens"] += embedding_tokens
            data["calls"] += 1
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
            os.replace(tmp, self.path)

    @property
    def spent_usd(self) -> float:
        return self.read()["spent_usd"]


class MeteredBackend:
    """Wraps a backend: refuses a call whose estimated cost would pass the limit and records the real usage."""

    def __init__(self, inner: Backend, ledger: UsageLedger, budget_usd: float, price_input: float, price_output: float, price_embedding: float):
        self._inner, self._ledger, self._budget = inner, ledger, budget_usd
        self._p_in, self._p_out, self._p_emb = price_input / 1e6, price_output / 1e6, price_embedding / 1e6
        self.id, self.host = inner.id, inner.host

    def _check(self, estimated_cost: float):
        spent = self._ledger.spent_usd
        if spent + estimated_cost > self._budget:
            raise BudgetExceededError(
                f"the limit of ${self._budget:.2f} for this project would be exceeded (spent ${spent:.4f}, "
                f"this call is estimated at ${estimated_cost:.4f}). Raise the limit with `beacon cloud enable` or use a local model.")

    def chat(self, model: str, system: str, user: str, *, num_ctx: int, max_tokens: int, temperature: float, seed: int,
             max_seconds: Optional[float] = None, should_stop: Optional[Callable[[], bool]] = None) -> RawChat:
        est_in = estimate_tokens(len(system) + len(user))
        self._check(est_in * self._p_in + max_tokens * self._p_out)
        raw = self._inner.chat(model, system, user, num_ctx=num_ctx, max_tokens=max_tokens, temperature=temperature, seed=seed,
                               max_seconds=max_seconds, should_stop=should_stop)
        tokens_in, tokens_out = raw.prompt_tokens or est_in, raw.answer_tokens or estimate_tokens(len(raw.content))
        self._ledger.add(input_tokens=tokens_in, output_tokens=tokens_out, cost_usd=tokens_in * self._p_in + tokens_out * self._p_out)
        return raw

    def embed(self, model: str, texts: List[str], *, num_ctx: int) -> List[List[float]]:
        estimated = estimate_tokens(sum(len(t) for t in texts))
        self._check(estimated * self._p_emb)
        vectors = self._inner.embed(model, texts, num_ctx=num_ctx)
        tokens = getattr(self._inner, "last_embedding_tokens", 0) or estimated
        self._ledger.add(embedding_tokens=tokens, cost_usd=tokens * self._p_emb)
        return vectors

    def list_models(self, timeout=None):
        return self._inner.list_models(timeout)

    def health(self, timeout: float = 3):
        return self._inner.health(timeout)

    def install_hint(self, model: str) -> str:
        return self._inner.install_hint(model)

    def start_hint(self) -> str:
        return self._inner.start_hint()
