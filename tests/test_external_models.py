"""OpenAI-compatible backend, external-server requirements, spending limit and per-project configuration."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import httpx  # noqa: E402
import pytest  # noqa: E402
import yaml  # noqa: E402
from typer.testing import CliRunner  # noqa: E402

from core import cli  # noqa: E402
from core.config import CloudSettings, load_config, resolve_ai_config, set_api_key, set_project_ai  # noqa: E402
from core.engine.backends import (  # noqa: E402
    BackendAuthError,
    BackendRateLimitError,
    BackendUnavailableError,
    BudgetExceededError,
    CloudNotConfiguredError,
    InputRejectedError,
    LLMCancelledError,
    MeteredBackend,
    ModelNotFoundError,
    OpenAICompatibleBackend,
    UsageLedger,
    create_backend,
    is_local_host,
)

CLOUD = CloudSettings(consent=True, consent_date="2026-10-24", budget_usd=1.0, price_input_per_million=1.0,
                      price_output_per_million=2.0, price_embedding_per_million=0.5)


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


# ---- what an external server requires ----

@pytest.mark.parametrize("host, local", [
    ("http://localhost:11434", True), ("http://127.0.0.1:8080/v1", True), ("http://192.168.1.20:1234/v1", True), ("http://ollama:11434", True),
    ("http://host.docker.internal:11434", True), ("https://api.openai.com/v1", False), ("https://openrouter.ai/api/v1", False), ("http://8.8.8.8/v1", False),
])
def test_locality_of_a_host(host, local):
    assert is_local_host(host) is local


def test_local_servers_need_nothing_but_external_ones_need_everything():
    assert create_backend("openai", "http://localhost:8080/v1").host == "http://localhost:8080/v1"
    with pytest.raises(CloudNotConfiguredError) as e:
        create_backend("openai", "https://api.openai.com/v1")
    assert "consent" in str(e.value) and "spending limit" in str(e.value) and "API key" in str(e.value)
    with pytest.raises(CloudNotConfiguredError):
        create_backend("openai", "https://api.openai.com/v1", api_key="k", cloud=CloudSettings(consent=True, budget_usd=5))  # no prices
    with pytest.raises(CloudNotConfiguredError):
        create_backend("openai", "http://api.openai.com/v1", api_key="k", cloud=CLOUD)  # not https
    assert isinstance(create_backend("openai", "https://api.openai.com/v1", api_key="k", cloud=CLOUD, data_dir=None), OpenAICompatibleBackend)


# ---- spending limit ----

class _Inner:
    id, host = "openai", "https://api.example.com/v1"

    def chat(self, *a, **k):
        from core.engine.backends import RawChat
        return RawChat("ok", 1000, 200)

    def embed(self, model, texts, *, num_ctx):
        return [[0.1] for _ in texts]


def test_calls_are_recorded_and_refused_once_the_limit_would_be_passed(tmp_path):
    ledger = UsageLedger(tmp_path)
    b = MeteredBackend(_Inner(), ledger, budget_usd=0.01, price_input=1.0, price_output=2.0, price_embedding=0.5)
    b.chat("m", "s", "u", num_ctx=8192, max_tokens=100, temperature=0, seed=1)
    assert ledger.read()["spent_usd"] == pytest.approx(1000 * 1e-6 + 200 * 2e-6) and ledger.read()["calls"] == 1
    big = "x" * 30000  # ~8,600 estimated tokens: $0.0086 + output reserve would pass $0.01 with what is already spent
    with pytest.raises(BudgetExceededError):
        b.chat("m", big, "u", num_ctx=8192, max_tokens=100, temperature=0, seed=1)
    assert ledger.read()["calls"] == 1  # nothing was sent or recorded


def test_embeddings_are_metered_too(tmp_path):
    ledger = UsageLedger(tmp_path)
    b = MeteredBackend(_Inner(), ledger, budget_usd=0.001, price_input=1.0, price_output=1.0, price_embedding=1.0)
    b.embed("e", ["a" * 350], num_ctx=2048)
    assert ledger.read()["embedding_tokens"] == 100
    with pytest.raises(BudgetExceededError):
        b.embed("e", ["a" * 350_000], num_ctx=2048)


# ---- per-project configuration ----

def _config(tmp_path, project_ai=None):
    path = tmp_path / "config.yaml"
    project = {"id": "p", "name": "p", "repo_path": str(tmp_path)}
    if project_ai:
        project["ai"] = project_ai
    path.write_text(yaml.safe_dump({"ai_provider": {"provider": "ollama", "host": "http://localhost:11434", "embedding_model": "e", "llm_model": "l"},
                                    "projects": [project]}), encoding="utf-8")
    return path


def test_a_project_without_override_uses_the_global_configuration(tmp_path):
    cfg = load_config(_config(tmp_path))
    assert resolve_ai_config("p", cfg).provider == "ollama" and resolve_ai_config(None, cfg).host == "http://localhost:11434"


def test_a_project_override_takes_its_models_cloud_settings_and_key(tmp_path):
    ai = {"provider": "openai", "host": "https://api.example.com/v1", "embedding_model": "emb", "llm_model": "big", "llm_num_ctx": 100000,
          "cloud": {"consent": True, "consent_date": "2026-10-24", "budget_usd": 5, "price_input_per_million": 1, "price_output_per_million": 2, "price_embedding_per_million": 0.1}}
    cfg = load_config(_config(tmp_path, ai))
    creds = tmp_path / "credentials.yaml"
    set_api_key("p", "sk-123", creds)
    resolved = resolve_ai_config("p", cfg, credentials_path=creds)
    assert (resolved.provider, resolved.llm_model, resolved.llm_num_ctx, resolved.api_key) == ("openai", "big", 100000, "sk-123")
    assert resolved.cloud.budget_usd == 5 and resolved.cloud.consent
    assert "api_key" not in resolved.public_dict()


def test_consent_must_be_a_real_true_and_numbers_must_be_positive(tmp_path):
    cfg = load_config(_config(tmp_path, {"provider": "openai", "host": "https://x.com/v1", "cloud": {"consent": "yes", "budget_usd": -3}}))
    cloud = resolve_ai_config("p", cfg, credentials_path=tmp_path / "none.yaml").cloud
    assert cloud.consent is False and cloud.budget_usd == 0.0


# ---- the command ----

def _enable(tmp_path, monkeypatch, *args, input_text=None):
    path = _config(tmp_path)
    creds = tmp_path / "credentials.yaml"
    from core import config as config_module
    for name in ("CONFIG_PATH",):
        monkeypatch.setattr(config_module, name, path)
    monkeypatch.setattr(config_module, "CREDENTIALS_PATH", creds)
    monkeypatch.setattr(cli, "_resolve_or_exit", lambda pid: None)
    monkeypatch.setattr(cli, "set_project_ai", lambda pid, ai: set_project_ai(pid, ai, path))
    monkeypatch.setattr(cli, "set_api_key", lambda pid, key: set_api_key(pid, key, creds))
    monkeypatch.setattr(cli, "get_api_key", lambda pid: None)
    result = CliRunner().invoke(cli.app, ["cloud", "enable", "p", *args], input=input_text)
    return result, path, creds


BASE = ["--host", "https://api.example.com/v1", "--embedding-model", "emb", "--llm-model", "big"]


def test_enabling_an_external_server_requires_limit_prices_and_acceptance(tmp_path, monkeypatch):
    result, path, _ = _enable(tmp_path, monkeypatch, *BASE)
    assert result.exit_code == 1 and "--budget-usd" in result.output
    assert "ai" not in yaml.safe_load(path.read_text(encoding="utf-8"))["projects"][0]

    flags = [*BASE, "--budget-usd", "5", "--price-input", "1", "--price-output", "2", "--price-embedding", "0.1"]
    result, path, _ = _enable(tmp_path, monkeypatch, *flags, input_text="n\n")
    assert result.exit_code == 1 and "ai" not in yaml.safe_load(path.read_text(encoding="utf-8"))["projects"][0]

    result, path, creds = _enable(tmp_path, monkeypatch, *flags, input_text="y\nsk-secret\n")
    assert result.exit_code == 0
    saved = yaml.safe_load(path.read_text(encoding="utf-8"))["projects"][0]["ai"]
    assert saved["cloud"]["consent"] is True and saved["cloud"]["budget_usd"] == 5 and "sk-secret" not in path.read_text(encoding="utf-8")
    assert yaml.safe_load(creds.read_text(encoding="utf-8"))["api_keys"]["p"] == "sk-secret"


def test_an_external_server_must_use_https(tmp_path, monkeypatch):
    flags = ["--host", "http://api.example.com/v1", "--embedding-model", "e", "--llm-model", "l", "--budget-usd", "5", "--price-input", "1",
             "--price-output", "2", "--price-embedding", "1", "--accept-risks"]
    result, _, _ = _enable(tmp_path, monkeypatch, *flags)
    assert result.exit_code == 1 and "https" in result.output


def test_a_local_openai_compatible_server_needs_no_consent(tmp_path, monkeypatch):
    result, path, _ = _enable(tmp_path, monkeypatch, "--host", "http://localhost:8080/v1", "--embedding-model", "e", "--llm-model", "l")
    assert result.exit_code == 0
    assert "cloud" not in yaml.safe_load(path.read_text(encoding="utf-8"))["projects"][0]["ai"]


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
        assert not (tmp_path / "data" / "usage.json").exists()  # a local server is not metered
    finally:
        server.shutdown()
