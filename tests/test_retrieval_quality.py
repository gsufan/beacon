"""Cambios de calidad de búsqueda (2026-09, medidos con tools/eval_retrieval.py):
formato por modelo de embeddings, embeddings por lotes, reconstrucción del
índice al cambiar de modelo, penalización de tests, contexto adaptativo y
archivos vacíos. Ollama se reemplaza por clientes falsos."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import git  # noqa: E402
import ollama  # noqa: E402
import pytest  # noqa: E402

from core.config import AIProviderConfig  # noqa: E402
from core.engine.chunker import chunk_source  # noqa: E402
from core.engine.embeddings import embed_query, profile_for  # noqa: E402
from core.engine import indexer as indexer_module  # noqa: E402
from core.engine.indexer import CodebaseIndexer, IndexModelMismatchError, embedding_text  # noqa: E402
from core.engine.rag_engine import RAGEngine, TEST_DISTANCE_PENALTY  # noqa: E402
from core.projects import ProjectContext  # noqa: E402


def _ai(model):
    return AIProviderConfig(provider="ollama", ollama_host="http://127.0.0.1:9", embedding_model=model, llm_model="l")


class _FakeEmbed:
    """Vectores de tamaño fijo por modelo (como la dimensión real de cada uno)."""
    def __init__(self, dim=3, fail_batches=False):
        self.dim, self.fail_batches, self.calls = dim, fail_batches, []

    def embed(self, model, input, **kwargs):
        self.calls.append((model, list(input)))
        if self.fail_batches and len(input) > 1:
            raise ollama.ResponseError("input length exceeds the context length", 500)
        return {"embeddings": [[0.1] * self.dim for _ in input]}


def _repo(tmp_path, files):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    repo = git.Repo.init(repo_dir)
    for rel, content in files.items():
        (repo_dir / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo_dir / rel).write_text(content)
        repo.index.add([rel])
    repo.index.commit("init")
    return ProjectContext(id="t", name="t", repo_path=str(repo_dir),
                          chroma_dir=str(tmp_path / "data" / "chroma_db"), docs_dir=str(tmp_path / "data" / "docs"))


# ------------------------------------------------ formato por modelo ----

def test_each_model_gets_its_own_query_and_document_format():
    assert profile_for("nomic-embed-text").query_prefix == "search_query: "
    assert profile_for("nomic-embed-text:latest").document_prefix == "search_document: "
    assert profile_for("qwen3-embedding:0.6b").query_prefix.startswith("Instruct:")
    assert profile_for("qwen3-embedding:0.6b").document_prefix == ""
    assert profile_for("modelo-desconocido").query_prefix == ""


def test_query_is_sent_with_model_prefix_and_no_silent_truncation():
    fake = _FakeEmbed()
    embed_query(fake, "nomic-embed-text", "¿qué hace f?")
    assert fake.calls[0][1] == ["search_query: ¿qué hace f?"]


# ------------------------------------------------ indexado por lotes ----

def test_file_chunks_are_embedded_in_a_single_batch(tmp_path):
    ctx = _repo(tmp_path, {"a.py": "def f():\n    return 1\n\n\ndef g():\n    return 2\n"})
    idx = CodebaseIndexer(ctx, _ai("qwen3-embedding:0.6b"))
    idx.client_ollama = fake = _FakeEmbed()
    idx.sync()
    assert len(fake.calls) == 1 and len(fake.calls[0][1]) >= 2


def test_batch_rejected_for_length_falls_back_to_per_chunk(tmp_path):
    ctx = _repo(tmp_path, {"a.py": "def f():\n    return 1\n\n\ndef g():\n    return 2\n"})
    idx = CodebaseIndexer(ctx, _ai("qwen3-embedding:0.6b"))
    idx.client_ollama = _FakeEmbed(fail_batches=True)
    result = idx.sync()
    assert result["chunks_insertados"] >= 2


def test_empty_files_are_not_indexed():
    assert chunk_source("pkg/__init__.py", "") == []
    assert chunk_source("pkg/__init__.py", "  \n\n") == []


# ------------------------------------------- cambio de modelo ----

def test_changing_embedding_model_rebuilds_index_with_new_dimension(tmp_path):
    ctx = _repo(tmp_path, {"a.py": "def f():\n    return 1\n"})
    old = CodebaseIndexer(ctx, _ai("nomic-embed-text"))
    old.client_ollama = _FakeEmbed(dim=3)
    old.sync()

    new = CodebaseIndexer(ctx, _ai("qwen3-embedding:0.6b"))
    new.client_ollama = _FakeEmbed(dim=4)  # otra dimensión, como 768 → 1024
    result = new.sync()  # sin commits nuevos: igual debe reconstruir

    assert "índice reconstruido" in result["modelo_embeddings"]
    assert new.indexed_embedding_model() == "qwen3-embedding:0.6b"
    vectors = new.collection.get(include=["embeddings"])["embeddings"]
    assert len(vectors) > 0 and all(len(v) == 4 for v in vectors)


def test_querying_an_index_built_with_another_model_gives_a_clear_error(tmp_path):
    ctx = _repo(tmp_path, {"a.py": "def f():\n    return 1\n"})
    idx = CodebaseIndexer(ctx, _ai("nomic-embed-text"))
    idx.client_ollama = _FakeEmbed()
    idx.sync()
    engine = RAGEngine(ctx, _ai("qwen3-embedding:0.6b"))
    with pytest.raises(IndexModelMismatchError, match="beacon sync"):
        engine.retrieve("¿qué hace f?")


# ------------------------------------------- formato del índice ----

def test_embedded_text_carries_the_file_path_but_the_document_is_only_code(tmp_path):
    ctx = _repo(tmp_path, {"src/shipping/quote.go": "package main\n\nfunc GetQuote() int {\n\treturn 1\n}\n"})
    idx = CodebaseIndexer(ctx, _ai("qwen3-embedding:0.6b"))
    fake = _FakeEmbed()
    idx.client_ollama = fake
    idx.sync()
    embedded = [text for _, batch in fake.calls for text in batch]
    assert any("src/shipping/quote.go" in t and "func GetQuote" in t for t in embedded)
    documents = idx.collection.get(include=["documents"])["documents"]
    assert documents and not any("src/shipping/quote.go" in d for d in documents)


def test_embedding_text_puts_the_path_first():
    assert embedding_text({"file_path": "a/b.py"}, "def f(): ...").startswith("a/b.py\n")


def test_python_overload_stubs_are_not_indexed():
    source = (
        "from typing import overload\n\n"
        "class A:\n"
        "    @overload\n    def __init__(self, x: int) -> None: ...\n"
        "    @typing.overload\n    def __init__(self, x: str) -> None: ...\n"
        "    def __init__(self, x):\n        self.x = x\n"
    )
    chunks = chunk_source("a.py", source)
    inits = [c for c in chunks if c.name == "__init__"]
    assert len(inits) == 1 and "self.x = x" in inits[0].code
    assert not any("@overload" in c.code for c in chunks if c.chunk_type == "raw")


def test_index_built_with_an_older_format_is_rebuilt(tmp_path, monkeypatch):
    ctx = _repo(tmp_path, {"a.py": "def f():\n    return 1\n"})
    monkeypatch.setattr(indexer_module, "INDEX_FORMAT", 1)
    old = CodebaseIndexer(ctx, _ai("qwen3-embedding:0.6b"))
    old.client_ollama = _FakeEmbed()
    old.sync()

    monkeypatch.setattr(indexer_module, "INDEX_FORMAT", 2)
    new = CodebaseIndexer(ctx, _ai("qwen3-embedding:0.6b"))
    new.client_ollama = _FakeEmbed()
    result = new.sync()  # sin commits nuevos: igual debe reconstruir
    assert result["formato_indice"] == "1 → 2 (índice reconstruido)"
    assert new.sync()["status"] == "sin_cambios"


# ------------------------------------ penalización de tests y contexto ----

class _Coll:
    """Colección falsa: devuelve candidatos con distancias fijas."""
    def __init__(self, rows):
        self.rows = rows

    def count(self):
        return len(self.rows)

    def query(self, query_embeddings, n_results):
        rows = self.rows[:n_results]
        return {"ids": [[f"{p}::{n}" for p, n, _ in rows]], "documents": [["code"] * len(rows)],
                "metadatas": [[{"file_path": p, "name": n, "chunk_type": "function"} for p, n, _ in rows]],
                "distances": [[d for _, _, d in rows]]}


def _engine_with(tmp_path, rows):
    ctx = _repo(tmp_path, {"a.py": "x = 1\n"})
    engine = RAGEngine(ctx, _ai("qwen3-embedding:0.6b"))
    engine.client_ollama = _FakeEmbed()
    engine.collection = _Coll(rows)
    engine.index_model = "qwen3-embedding:0.6b"
    return engine


def test_tests_rank_below_source_unless_question_is_about_tests(tmp_path):
    rows = [("tests/test_sessions.py", "test_redirect", 0.30), ("src/sessions.py", "rebuild_method", 0.33)]
    engine = _engine_with(tmp_path, rows)
    assert engine.retrieve("¿Por qué POST pasa a GET tras un 303?", top_k=1)[0].name == "rebuild_method"
    assert engine.retrieve("¿Qué test cubre el cambio de POST a GET?", top_k=1)[0].name == "test_redirect"
    assert TEST_DISTANCE_PENALTY > 0.33 - 0.30


def test_adaptive_context_adds_near_ties_but_not_distant_results(tmp_path):
    rows = [(f"src/m{i}.py", f"f{i}", d) for i, d in enumerate([0.20, 0.21, 0.22, 0.23, 0.24, 0.25, 0.26, 0.40])]
    engine = _engine_with(tmp_path, rows)
    names = [c.name for c in engine.select_context("pregunta", top_k=5)]
    assert names[:5] == ["f0", "f1", "f2", "f3", "f4"]
    assert "f5" in names and "f6" in names  # casi empatados con el 5º
    assert "f7" not in names  # claramente menos relevante
