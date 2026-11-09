"""The periodic check detects an outage of the model server (and its recovery) without any user asking."""

import logging

from core.config import AIProviderConfig
from core.engine.backends import BackendUnavailableError, RawChat
from core.monitor import ModelMonitor

AI = AIProviderConfig(provider="ollama", host="http://127.0.0.1:11434", embedding_model="emb", llm_model="llm")


class _Backend:
    def __init__(self, state):
        self.state = state

    def list_models(self, timeout=None):
        if self.state["down"]:
            raise BackendUnavailableError("connection refused")
        return self.state["models"]

    def embed(self, model, texts, *, num_ctx):
        return [[0.1, 0.2] for _ in texts]

    def chat(self, model, system, user, **kwargs):
        return RawChat("o", prompt_tokens=2, answer_tokens=1)


def _monitor(state):
    return ModelMonitor(lambda: AI, backend_factory=lambda provider, host: _Backend(state))


def test_detects_outage_and_recovery(caplog):
    state = {"down": False, "models": ["emb:latest", "llm"]}
    monitor = _monitor(state)
    assert monitor.check()["ok"]
    state["down"] = True
    with caplog.at_level(logging.WARNING, logger="beacon.monitor"):
        assert not monitor.check()["ok"]
        assert not monitor.check()["ok"]
    assert monitor.state()["consecutive_failures"] == 2
    assert "no responde" in caplog.text
    state["down"] = False
    with caplog.at_level(logging.WARNING, logger="beacon.monitor"):
        assert monitor.check()["ok"]
    assert "se recuperó" in caplog.text
    s = monitor.state()
    assert s["ok"] and s["consecutive_failures"] == 0 and len(s["history"]) == 4


def test_missing_model_counts_as_failure():
    monitor = _monitor({"down": False, "models": ["emb"]})
    result = monitor.check()
    assert not result["ok"] and "llm" in result["error"]
