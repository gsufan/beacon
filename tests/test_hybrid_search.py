"""Búsqueda híbrida (2026-10, medida con eval/*-identificadores.yaml): si la
pregunta nombra un identificador, los fragmentos que se llaman así o lo usan
entran como candidatos con un descuento de distancia. Ollama se reemplaza por
un cliente falso que da el mismo vector a todo, así que el orden lo deciden
solo los descuentos y la penalización a tests."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import git  # noqa: E402

from core.config import AIProviderConfig  # noqa: E402
from core.engine.indexer import CodebaseIndexer  # noqa: E402
from core.engine.rag_engine import RAGEngine  # noqa: E402
from core.projects import ProjectContext  # noqa: E402

AI = AIProviderConfig(provider="ollama", ollama_host="http://127.0.0.1:9",
                      embedding_model="qwen3-embedding:0.6b", llm_model="l")

SOURCES = {
    "src/utils.py": (
        "def super_len(o):\n    return len(o)\n\n\n"
        "def helper():\n    return 0\n"
    ),
    "src/sessions.py": (
        "class TooManyRedirects(Exception):\n    pass\n\n\n"
        "def resolve_redirects(resp, max_redirects):\n"
        "    if len(resp.history) >= max_redirects:\n"
        "        raise TooManyRedirects()\n"
    ),
    "tests/test_utils.py": (
        "def test_super_len_with_len():\n    assert super_len([1]) == 1\n\n\n"
        "def test_super_len_with_tell():\n    assert super_len([]) == 0\n"
    ),
}


class _SameVector:
    def embed(self, model, input, **kwargs):
        return {"embeddings": [[0.3, 0.4, 0.5] for _ in input]}


def _engine(tmp_path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    repo = git.Repo.init(repo_dir)
    for rel, content in SOURCES.items():
        (repo_dir / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo_dir / rel).write_text(content)
        repo.index.add([rel])
    repo.index.commit("init")
    ctx = ProjectContext(id="t", name="t", repo_path=str(repo_dir),
                         chroma_dir=str(tmp_path / "data" / "chroma_db"), docs_dir=str(tmp_path / "data" / "docs"))
    idx = CodebaseIndexer(ctx, AI)
    idx.client_ollama = _SameVector()
    idx.sync()
    engine = RAGEngine(ctx, AI)
    engine.client_ollama = _SameVector()
    return engine


def test_detects_identifiers_but_not_ordinary_words():
    ids = RAGEngine.question_identifiers(
        "¿Dónde se usa max_redirects y por qué TooManyRedirects usa DEFAULT_CA_BUNDLE_PATH en Redis?")
    assert ids == ["max_redirects", "TooManyRedirects", "DEFAULT_CA_BUNDLE_PATH"]
    assert RAGEngine.question_identifiers("¿Cómo se envía la petición HTTP con POST?") == []


def test_named_function_beats_tests_that_repeat_its_name(tmp_path):
    engine = _engine(tmp_path)
    top = engine.retrieve("¿Para qué sirve super_len?", top_k=1)[0]
    assert (top.file_path, top.name) == ("src/utils.py", "super_len")


def test_usage_question_prefers_the_code_that_uses_the_identifier(tmp_path):
    engine = _engine(tmp_path)
    top = engine.retrieve("¿Dónde se lanza TooManyRedirects?", top_k=1)[0]
    assert top.name == "resolve_redirects"
    # Preguntar qué es, en cambio, apunta a la definición.
    assert engine.retrieve("¿Qué es TooManyRedirects?", top_k=1)[0].name == "TooManyRedirects"


def test_question_without_identifiers_is_unchanged(tmp_path):
    engine = _engine(tmp_path)
    assert engine._identifier_matches("¿Cómo se calcula el largo?", [0.3, 0.4, 0.5]) == {}


def test_call_candidates_skip_definitions():
    code = "def outer():\n    def hook(r):\n        return r\n    return send(hook)\n"
    assert RAGEngine._extract_call_candidates(code, "outer") == ["send"]
    js = "function main() {\n  function local(x) { return x }\n  return convert(local)\n}"
    assert RAGEngine._extract_call_candidates(js, "main") == ["convert"]
