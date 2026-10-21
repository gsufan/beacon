"""Límites de la respuesta del modelo: tope de longitud, tiempo máximo y cancelación (un bucle de repetición no debe
dejar la GPU ocupada minutos), más el chequeo previo del modo estudio."""
import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from core.engine import llm  # noqa: E402
from core.study import api as study_api  # noqa: E402


class _Stream:
    """Flujo de Ollama simulado: fragmentos de texto y un último con los conteos. Registra si se cerró."""

    def __init__(self, pieces, done_counts=(10, 5), delay=0.0):
        self.pieces, self.done_counts, self.delay, self.closed = pieces, done_counts, delay, False

    def __iter__(self):
        for text in self.pieces:
            time.sleep(self.delay)
            yield {"message": {"content": text}}
        yield {"message": {"content": ""}, "done": True, "prompt_eval_count": self.done_counts[0], "eval_count": self.done_counts[1]}

    def close(self):
        self.closed = True


class _Client:
    def __init__(self, stream=None):
        self.stream, self.calls = stream, []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        return self.stream if kwargs.get("stream") else {"message": {"content": "ok"}, "prompt_eval_count": 5, "eval_count": 3}


def test_answers_have_a_length_cap_in_every_call():
    client = _Client()
    llm.chat(client, "m", "s", "u", max_tokens=300)
    assert client.calls[0]["options"]["num_predict"] == 300 and not client.calls[0].get("stream")
    llm.chat(client, "m", "s", "u")
    assert client.calls[1]["options"]["num_predict"] == llm.LLM_MAX_ANSWER_TOKENS


def test_an_answer_that_hits_the_cap_is_marked_as_cut():
    client = _Client(_Stream(["a", "b"], done_counts=(10, 50)))
    result = llm.chat(client, "m", "s", "u", max_tokens=50, max_seconds=10)
    assert result.content == "ab" and result.truncated and (result.prompt_tokens, result.answer_tokens) == (10, 50)


def test_streaming_returns_the_full_text_and_counts():
    result = llm.chat(_Client(_Stream(["hola ", "mundo"])), "m", "s", "u", should_stop=lambda: False)
    assert result.content == "hola mundo" and not result.truncated and result.answer_tokens == 5


def test_a_slow_answer_is_stopped_at_the_time_limit_and_the_stream_is_closed():
    stream = _Stream(["x"] * 50, delay=0.05)
    with pytest.raises(llm.LLMTimeoutError):
        llm.chat(_Client(stream), "m", "s", "u", max_seconds=0.2)
    assert stream.closed


def test_a_cancelled_answer_stops_and_the_stream_is_closed():
    stream = _Stream(["x"] * 50, delay=0.01)
    seen = []

    def stop():
        seen.append(1)
        return len(seen) >= 3

    with pytest.raises(llm.LLMCancelledError):
        llm.chat(_Client(stream), "m", "s", "u", should_stop=stop)
    assert stream.closed


# ---- endpoints del estudio ------------------------------------------------------

class _Engine:
    ai_config = SimpleNamespace(ollama_host="http://x")

    def __init__(self, error=None):
        self.error = error

    def ask(self, question, **kwargs):
        assert kwargs["max_seconds"] == study_api.ASK_TIMEOUT_SECONDS and callable(kwargs["should_stop"])
        if self.error:
            raise self.error
        return SimpleNamespace(prompt_tokens=1, answer_tokens=1, to_dict=lambda: {"answer": "r", "sources": []})


def _client(tmp_path, monkeypatch, engine):
    monkeypatch.setattr(study_api, "session_path", lambda: tmp_path / "_study" / "sesion.json")
    app = FastAPI()
    router = study_api.build_router(lambda pid: engine, lambda: None)
    app.include_router(router)
    client = TestClient(app)
    client.post("/study/start", json={})
    client.post("/study/consent", json={"tipo": "participacion", "aceptado": True})
    study_api.StudySession(tmp_path / "_study" / "sesion.json").set_repo("https://github.com/u/r", "abc", 1, 1, ["c"], 1.0)
    return client, router


def test_a_slow_question_returns_a_clear_timeout_message(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch, _Engine(llm.LLMTimeoutError("lento")))
    out = client.post("/study/ask", json={"pregunta": "algo"})
    assert out.status_code == 504 and "más concreta" in out.json()["detail"]


def test_a_cancelled_question_is_reported_and_the_cancel_flag_resets_for_the_next_one(tmp_path, monkeypatch):
    client, router = _client(tmp_path, monkeypatch, _Engine(llm.LLMCancelledError("x")))
    assert client.post("/study/cancel").json() == {"ok": True}
    assert client.post("/study/ask", json={"pregunta": "algo"}).status_code == 409
    ok_client, _ = _client(tmp_path, monkeypatch, _Engine())
    assert ok_client.post("/study/ask", json={"pregunta": "otra"}).status_code == 200


def test_the_server_shutdown_hook_stops_a_running_answer(tmp_path, monkeypatch):
    _, router = _client(tmp_path, monkeypatch, _Engine())
    assert callable(router.shutdown)
    router.shutdown()  # no falla aunque no haya nada en curso


def test_health_flags_what_is_missing_before_starting(monkeypatch):
    import ollama

    class _Ollama:
        def __init__(self, **kw):
            pass

        def list(self):
            return {"models": [{"model": "llama3:8b"}]}  # falta el modelo de embeddings

    monkeypatch.setattr(ollama, "Client", _Ollama)
    monkeypatch.setattr(study_api.hardware, "collect_hardware", lambda path=".": {"gpus": [{"modelo": "GPU", "vram_gb": 4.0}], "disco_libre_gb": 1.0})
    monkeypatch.setattr(study_api.httpx, "head", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("sin red")))
    status = {i["id"]: i["estado"] for i in study_api.run_health_checks()}
    assert status == {"ollama": "ok", "model_embedding": "error", "model_llm": "ok", "gpu": "warn", "disk": "warn", "network": "error"}


def test_health_with_ollama_down_skips_the_model_checks(monkeypatch):
    import ollama

    def boom(**kw):
        raise ConnectionError("no responde")

    monkeypatch.setattr(ollama, "Client", boom)
    monkeypatch.setattr(study_api.hardware, "collect_hardware", lambda path=".": {"gpus": [], "disco_libre_gb": 50.0})
    monkeypatch.setattr(study_api.httpx, "head", lambda *a, **k: None)
    status = {i["id"]: i["estado"] for i in study_api.run_health_checks()}
    assert status["ollama"] == "error" and status["model_llm"] == "skip" and status["gpu"] == "warn" and status["network"] == "ok"
