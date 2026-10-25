"""OpenAI-compatible backend (LM Studio, llama.cpp, vLLM) and the local-only rule."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import httpx  # noqa: E402
import pytest  # noqa: E402

from core.engine.backends import (  # noqa: E402
    BackendAuthError,
    BackendRateLimitError,
    BackendUnavailableError,
    ExternalServerError,
    InputRejectedError,
    LLMCancelledError,
    ModelNotFoundError,
    OpenAICompatibleBackend,
    create_backend,
    is_local_host,
)

def _backend(handler, **kw):
    return OpenAICompatibleBackend("https://api.example.com/v1", "sk-test", transport=httpx.MockTransport(handler), sleep=lambda s: None, **kw)


def _chat(b, **kw):
    return b.chat("m", "sys", "user", num_ctx=8192, max_tokens=50, temperature=0.2, seed=1, **kw)


def _completion(content="hola", usage=(12, 3)):
    return {"choices": [{"message": {"content": content}}], "usage": {"prompt_tokens": usage[0], "completion_tokens": usage[1]}}


# ---- the adapter ----

def test_chat_sends_the_key_and_returns_content_and_usage():
    seen = {}

    def handler(request):
        seen["auth"], seen["body"] = request.headers["authorization"], json.loads(request.content)
        return httpx.Response(200, json=_completion())

    raw = _chat(_backend(handler))
    assert (raw.content, raw.prompt_tokens, raw.answer_tokens) == ("hola", 12, 3)
    assert seen["auth"] == "Bearer sk-test" and seen["body"]["max_tokens"] == 50 and seen["body"]["messages"][0]["role"] == "system"


def test_parameters_a_model_rejects_are_adapted_and_remembered():
    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        if "max_tokens" in body:
            return httpx.Response(400, json={"error": {"message": "Unsupported parameter: 'max_tokens'. Use 'max_completion_tokens' instead."}})
        if "temperature" in body:
            return httpx.Response(400, json={"error": {"message": "Unsupported value: 'temperature' does not support 0.2 with this model."}})
        return httpx.Response(200, json=_completion())

    b = _backend(handler)
    assert _chat(b).content == "hola"
    assert "max_completion_tokens" in bodies[-1] and "temperature" not in bodies[-1]
    n = len(bodies)
    _chat(b)
    assert len(bodies) == n + 1  # learned: no more retries


@pytest.mark.parametrize("status, message, expected", [
    (401, "Incorrect API key", BackendAuthError),
    (404, "The model `x` does not exist", ModelNotFoundError),
    (400, "This model's maximum context length is 8192 tokens", InputRejectedError),
])
def test_errors_are_translated(status, message, expected):
    b = _backend(lambda r: httpx.Response(status, json={"error": {"message": message}}))
    with pytest.raises(expected):
        _chat(b)


def test_rate_limits_are_retried_and_then_reported():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(429, json={"error": {"message": "slow down"}}, headers={"retry-after": "1"}) if len(calls) < 3 else httpx.Response(200, json=_completion())

    assert _chat(_backend(handler)).content == "hola" and len(calls) == 3
    with pytest.raises(BackendRateLimitError):
        _chat(_backend(lambda r: httpx.Response(429, json={"error": {"message": "slow down"}})))


def test_connection_failures_are_reported_as_unavailable():
    def handler(request):
        raise httpx.ConnectError("refused")

    with pytest.raises(BackendUnavailableError):
        _chat(_backend(handler))


def _sse(*events):
    return "".join(f"data: {json.dumps(e)}\n\n" for e in events) + "data: [DONE]\n\n"


def test_streaming_collects_text_and_usage_and_can_be_cancelled():
    stream = _sse({"choices": [{"delta": {"content": "ho"}}]}, {"choices": [{"delta": {"content": "la"}}]},
                  {"choices": [], "usage": {"prompt_tokens": 9, "completion_tokens": 2}})
    b = _backend(lambda r: httpx.Response(200, text=stream, headers={"content-type": "text/event-stream"}))
    raw = _chat(b, should_stop=lambda: False)
    assert (raw.content, raw.prompt_tokens, raw.answer_tokens) == ("hola", 9, 2)
    with pytest.raises(LLMCancelledError):
        _chat(b, should_stop=lambda: True)


def test_embeddings_are_batched_ordered_and_counted():
    sizes = []

    def handler(request):
        batch = json.loads(request.content)["input"]
        sizes.append(len(batch))
        data = [{"index": i, "embedding": [float(len(t)), 0.0]} for i, t in reversed(list(enumerate(batch)))]
        return httpx.Response(200, json={"data": data, "usage": {"total_tokens": len(batch) * 10}})

    b = _backend(handler)
    texts = ["a" * (i + 1) for i in range(150)]
    vectors = b.embed("e", texts, num_ctx=2048)
    assert sizes == [100, 50] and [v[0] for v in vectors] == [float(i + 1) for i in range(150)] and b.last_embedding_tokens == 1500


def test_list_models():
    b = _backend(lambda r: httpx.Response(200, json={"data": [{"id": "gpt-x"}, {"id": "emb-y"}]}))
    assert b.list_models() == ["gpt-x", "emb-y"] and b.health().reachable


# ---- locality: only this machine and private networks are supported ----

@pytest.mark.parametrize("host, local", [
    ("http://localhost:11434", True), ("http://127.0.0.1:8080/v1", True), ("http://192.168.1.20:1234/v1", True), ("http://ollama:11434", True),
    ("http://host.docker.internal:11434", True), ("https://api.openai.com/v1", False), ("https://openrouter.ai/api/v1", False), ("http://8.8.8.8/v1", False),
])
def test_locality_of_a_host(host, local):
    assert is_local_host(host) is local


def test_local_servers_are_created_and_external_ones_are_refused():
    assert create_backend("openai", "http://localhost:8080/v1").host == "http://localhost:8080/v1"
    assert create_backend("lmstudio", "").host == "http://localhost:1234/v1"
    with pytest.raises(ExternalServerError) as e:
        create_backend("openai", "https://api.openai.com/v1")
    assert "experimental" in str(e.value)
    with pytest.raises(ExternalServerError):
        create_backend("ollama", "https://ollama.example.com")


# ---- the whole engine against a local OpenAI-compatible server ----

def _fake_server():
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    requests_seen = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, payload):
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self._send({"data": [{"id": "emb"}, {"id": "chat"}]})

        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests_seen.append((self.path, data))
            if self.path.endswith("/embeddings"):
                self._send({"data": [{"index": i, "embedding": [0.1 * (i + 1), 0.2, 0.3]} for i, _ in enumerate(data["input"])], "usage": {"total_tokens": 5}})
            else:
                self._send(_completion("respuesta del servidor local"))

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, requests_seen


def test_the_engine_indexes_and_answers_through_an_openai_compatible_server(tmp_path):
    import git

    from core.config import AIProviderConfig
    from core.engine.indexer import CodebaseIndexer
    from core.engine.rag_engine import RAGEngine
    from core.projects import ProjectContext

    server, seen = _fake_server()
    try:
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        repo = git.Repo.init(repo_dir)
        (repo_dir / "a.py").write_text("def hola():\n    return 1\n")
        repo.index.add(["a.py"])
        repo.index.commit("init")
        ctx = ProjectContext(id="t", name="t", repo_path=str(repo_dir), chroma_dir=str(tmp_path / "data" / "chroma_db"), docs_dir=str(tmp_path / "data" / "docs"))
        ai = AIProviderConfig(provider="openai", host=f"http://127.0.0.1:{server.server_port}/v1", embedding_model="emb", llm_model="chat")
        CodebaseIndexer(ctx, ai).sync()
        answer = RAGEngine(ctx, ai).ask("¿Qué hace la función hola?")
        assert answer.answer == "respuesta del servidor local" and answer.prompt_tokens == 12
        paths = [p for p, _ in seen]
        assert any(p.endswith("/embeddings") for p in paths) and any(p.endswith("/chat/completions") for p in paths)
        chat_body = next(d for p, d in seen if p.endswith("/chat/completions"))
        assert chat_body["model"] == "chat" and chat_body["messages"][0]["role"] == "system"
    finally:
        server.shutdown()
