"""Configurable model context window (`llm_num_ctx`). The default is llama3:8b's (8,192); a model with more
context, on hardware that supports it, is configured without touching the code.
"""
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import yaml  # noqa: E402

from core.config import (  # noqa: E402
    DEFAULT_LLM_NUM_CTX, AIProviderConfig, load_config, update_ai_provider,
)
from core.engine import llm  # noqa: E402
from core.engine.rag_engine import RAGEngine, RetrievedChunk  # noqa: E402
from core.projects import ProjectContext  # noqa: E402

AI = AIProviderConfig(provider="ollama", ollama_host="http://127.0.0.1:9",
                      embedding_model="e", llm_model="m")


class _FakeOllama:
    def __init__(self):
        self.chats = []

    def chat(self, **kwargs):
        self.chats.append(kwargs)
        return {"message": {"content": "r"}, "prompt_eval_count": 100, "eval_count": 10}


def _write_config(path, ai_extra=""):
    path.write_text(
        "ai_provider:\n  provider: ollama\n  ollama_host: http://localhost:11434\n"
        "  embedding_model: e\n  llm_model: m\n" + ai_extra + "projects: []\n", encoding="utf-8")



def test_default_window_is_the_one_measured_so_far(tmp_path):
    cfg = tmp_path / "config.yaml"
    _write_config(cfg)
    assert load_config(cfg).ai_provider.llm_num_ctx == DEFAULT_LLM_NUM_CTX == llm.LLM_NUM_CTX == 8192


def test_window_is_read_from_config(tmp_path):
    cfg = tmp_path / "config.yaml"
    _write_config(cfg, "  llm_num_ctx: 32768\n")
    assert load_config(cfg).ai_provider.llm_num_ctx == 32768


def test_invalid_window_falls_back_to_default(tmp_path):
    for bad in ("abc", "100", "-5", "true", "null"):
        cfg = tmp_path / "config.yaml"
        _write_config(cfg, f"  llm_num_ctx: {bad}\n")
        assert load_config(cfg).ai_provider.llm_num_ctx == DEFAULT_LLM_NUM_CTX, bad


def test_saving_provider_from_the_ui_keeps_the_configured_window(tmp_path):
    # the settings screen does not send the window: it must not be lost when saving
    cfg = tmp_path / "config.yaml"
    _write_config(cfg, "  llm_num_ctx: 16384\n")
    updated = update_ai_provider("ollama", "http://h:11434", "e2", "m2", path=cfg)
    assert updated.llm_num_ctx == 16384
    assert load_config(cfg).ai_provider.llm_num_ctx == 16384
    assert yaml.safe_load(cfg.read_text(encoding="utf-8"))["ai_provider"]["llm_model"] == "m2"


def test_window_can_be_changed_and_default_is_not_written(tmp_path):
    cfg = tmp_path / "config.yaml"
    _write_config(cfg)
    assert update_ai_provider("ollama", "h", "e", "m", path=cfg, llm_num_ctx=4096).llm_num_ctx == 4096
    assert "llm_num_ctx" in cfg.read_text(encoding="utf-8")
    update_ai_provider("ollama", "h", "e", "m", path=cfg, llm_num_ctx=DEFAULT_LLM_NUM_CTX)
    assert "llm_num_ctx" not in cfg.read_text(encoding="utf-8")



def test_chat_sends_the_requested_window():
    fake = _FakeOllama()
    llm.chat(fake, "m", "s", "u", num_ctx=32768)
    assert fake.chats[0]["options"]["num_ctx"] == 32768


def test_budget_grows_with_the_window():
    assert llm.prompt_budget_tokens(1024) == 8192 - 1024
    assert llm.prompt_budget_tokens(1024, 32768) == 32768 - 1024


def _engine(tmp_path, num_ctx):
    ctx = ProjectContext(id="t", name="t", repo_path=str(tmp_path),
                         chroma_dir=str(tmp_path / "chroma"), docs_dir=str(tmp_path / "docs"))
    engine = RAGEngine(ctx, replace(AI, llm_num_ctx=num_ctx))
    engine.client_ollama = fake = _FakeOllama()
    chunks = [RetrievedChunk(file_path=f"f{i}.py", chunk_type="function", name=f"f{i}", start_line=1,
                             end_line=10, code="x" * 6000, distance=0.1) for i in range(10)]
    engine.select_context = lambda q, top_k: chunks
    engine.expand_with_call_graph = lambda c: []
    return engine, fake


def test_a_larger_window_lets_more_context_reach_the_model(tmp_path):
    small, fake_small = _engine(tmp_path, 8192)
    large, fake_large = _engine(tmp_path, 32768)
    kept_small = len(small.ask("¿qué hace f0?", top_k=10).sources)
    kept_large = len(large.ask("¿qué hace f0?", top_k=10).sources)
    assert kept_large > kept_small
    assert fake_large.chats[0]["options"]["num_ctx"] == 32768
    sent = sum(len(m["content"]) for m in fake_large.chats[0]["messages"])
    assert llm.estimate_tokens("x" * sent) <= llm.prompt_budget_tokens(1024, 32768)
