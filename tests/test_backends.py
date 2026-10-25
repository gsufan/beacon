"""Model server backends: registry, configuration and the Ollama adapter's error translation."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import ollama  # noqa: E402
import pytest  # noqa: E402

from core.config import load_config, update_ai_provider  # noqa: E402
from core.engine.backends import (  # noqa: E402
    BackendError,
    BackendUnavailableError,
    InputRejectedError,
    ModelNotFoundError,
    OllamaBackend,
    available_providers,
    create_backend,
)


class _Client:
    def __init__(self, error=None, models=("llama3:8b",)):
        self.error, self.models = error, models

    def chat(self, **kwargs):
        if self.error:
            raise self.error
        return {"message": {"content": "hi"}, "prompt_eval_count": 7, "eval_count": 2}

    def embed(self, **kwargs):
        if self.error:
            raise self.error
        return {"embeddings": [[0.1, 0.2] for _ in kwargs["input"]]}

    def list(self):
        if self.error:
            raise self.error
        return {"models": [{"model": m} for m in self.models]}


def _chat(backend):
    return backend.chat("m", "s", "u", num_ctx=4096, max_tokens=10, temperature=0.2, seed=1)


def test_registry_lists_providers_and_rejects_unknown_ones():
    assert [p["id"] for p in available_providers()] == ["ollama"]
    assert create_backend("ollama", "http://h:1").host == "http://h:1"
    with pytest.raises(BackendError):
        create_backend("nope", "http://h:1")


def test_ollama_backend_returns_content_and_token_counts():
    raw = _chat(OllamaBackend(_Client()))
    assert (raw.content, raw.prompt_tokens, raw.answer_tokens) == ("hi", 7, 2)


@pytest.mark.parametrize("error, expected", [
    (ConnectionError("refused"), BackendUnavailableError),
    (ollama.ResponseError("model 'x' not found", 404), ModelNotFoundError),
    (ollama.ResponseError("input length exceeds the context length", 500), InputRejectedError),
])
def test_ollama_errors_are_translated_in_every_operation(error, expected):
    backend = OllamaBackend(_Client(error))
    for call in (lambda: _chat(backend), lambda: backend.embed("m", ["t"], num_ctx=2048), backend.list_models):
        with pytest.raises(expected):
            call()


def test_empty_embeddings_are_rejected():
    class Empty(_Client):
        def embed(self, **kwargs):
            return {"embeddings": [[] for _ in kwargs["input"]]}

    with pytest.raises(InputRejectedError):
        OllamaBackend(Empty()).embed("m", ["t"], num_ctx=2048)


def test_list_models_and_hints():
    backend = OllamaBackend(_Client(models=("a", "b:latest")))
    assert backend.list_models() == ["a", "b:latest"]
    assert backend.install_hint("a") == "ollama pull a" and backend.start_hint() == "ollama serve"


def test_config_reads_the_legacy_ollama_host_key_and_writes_host(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("ai_provider:\n  provider: ollama\n  ollama_host: http://old:11434\n  embedding_model: e\n  llm_model: l\nprojects: []\n", encoding="utf-8")
    assert load_config(path).ai_provider.host == "http://old:11434"
    update_ai_provider("ollama", "http://new:11434", "e", "l", path=path)
    text = path.read_text(encoding="utf-8")
    assert "host: http://new:11434" in text and "ollama_host" not in text
    assert load_config(path).ai_provider.host == "http://new:11434"


def test_config_without_a_host_uses_the_provider_default(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("ai_provider:\n  provider: ollama\n  embedding_model: e\n  llm_model: l\nprojects: []\n", encoding="utf-8")
    assert load_config(path).ai_provider.host == "http://localhost:11434"
