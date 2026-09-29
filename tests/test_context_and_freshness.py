"""Regresiones de la revisión de arquitectura (2026-09):

- 1a: el prompt del chat cabe en la ventana del modelo (num_ctx explícito y
  recorte previo), y las fuentes devueltas son las que vio el modelo.
- 1b: el índice corresponde exactamente al commit registrado (se lee desde
  git, no desde el disco, y el commit se fija al empezar el sync).
- 1c: un proceso con ChromaDB abierto ve lo que otro proceso indexó.
- 1d: la documentación de un archivo grande cubre el archivo completo.

Ollama se reemplaza por clientes falsos; el 1c usa un proceso hijo real.
"""

import subprocess
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import git  # noqa: E402

from core.config import AIProviderConfig  # noqa: E402
from core.engine import llm  # noqa: E402
from core.engine.chroma_utils import bump_index_version, get_chroma_client  # noqa: E402
from core.engine.doc_generator import DocGenerator  # noqa: E402
from core.engine.indexer import CodebaseIndexer  # noqa: E402
from core.engine.rag_engine import RAGEngine, RetrievedChunk  # noqa: E402
from core.projects import ProjectContext  # noqa: E402

SRC = str(Path(__file__).resolve().parents[1] / "src")
AI = AIProviderConfig(provider="ollama", ollama_host="http://127.0.0.1:9",
                      embedding_model="nomic-embed-text", llm_model="llama3:8b")


class _RecordingOllama:
    def __init__(self, content="## Propósito\nDoc de prueba."):
        self.chats = []
        self.content = content

    def embed(self, input, **kwargs):
        return {"embeddings": [[0.1, 0.2, 0.3] for _ in input]}

    def chat(self, **kwargs):
        self.chats.append(kwargs)
        return {"message": {"content": self.content}, "prompt_eval_count": 100, "eval_count": 50}


def _repo_and_ctx(tmp_path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    repo = git.Repo.init(repo_dir)
    ctx = ProjectContext(id="t", name="t", repo_path=str(repo_dir),
                         chroma_dir=str(tmp_path / "data" / "chroma_db"), docs_dir=str(tmp_path / "data" / "docs"))
    return repo, ctx


def _commit(repo, rel, content, msg):
    path = Path(repo.working_dir) / rel
    path.write_text(content)
    repo.index.add([rel])
    return repo.index.commit(msg).hexsha


# ------------------------------------------------------------------- 1a ----

def _chunk(name, size, expanded=False):
    return RetrievedChunk(file_path=f"{name}.py", chunk_type="function", name=name, start_line=1,
                          end_line=10, code="x" * size, distance=0.1, expanded=expanded)


def test_chat_always_sends_explicit_num_ctx():
    fake = _RecordingOllama()
    llm.chat(fake, "llama3:8b", "sistema", "usuario")
    assert fake.chats[0]["options"]["num_ctx"] == llm.LLM_NUM_CTX


def test_chat_warns_when_model_processed_far_less_than_sent(caplog):
    class Truncating(_RecordingOllama):
        def chat(self, **kwargs):
            return {"message": {"content": "r"}, "prompt_eval_count": 2060, "eval_count": 10}

    with caplog.at_level("WARNING", logger="beacon.llm"):
        llm.chat(Truncating(), "m", "s", "u" * 30000)  # ~8.500 tokens estimados
    assert any("truncó" in r.message for r in caplog.records)


def test_rag_prompt_fits_context_and_sources_match_what_model_saw(tmp_path):
    _, ctx = _repo_and_ctx(tmp_path)
    engine = RAGEngine(ctx, AI)
    engine.client_ollama = fake = _RecordingOllama(content="respuesta")
    # 10 fragmentos de ~6.000 caracteres (~1.700 tokens c/u): no caben todos en 8.192
    chunks = [_chunk(f"f{i}", 6000) for i in range(10)]
    engine.select_context = lambda q, top_k: chunks
    engine.expand_with_call_graph = lambda c: []

    result = engine.ask("¿qué hace f0?", top_k=10)

    sent = sum(len(m["content"]) for m in fake.chats[0]["messages"])
    assert llm.estimate_tokens("x" * sent) <= llm.prompt_budget_tokens(1024)
    assert 0 < len(result.sources) < 10
    assert [s.name for s in result.sources] == [f"f{i}" for i in range(len(result.sources))]  # por prioridad
    user_prompt = fake.chats[0]["messages"][1]["content"]
    assert all(f"'{s.name}'" in user_prompt for s in result.sources)
    assert f"'f{len(result.sources)}'" not in user_prompt


# ------------------------------------------------------------------- 1b ----

def test_index_uses_committed_content_not_uncommitted_working_tree(tmp_path):
    repo, ctx = _repo_and_ctx(tmp_path)
    head = _commit(repo, "a.py", "def version_commiteada():\n    return 1\n", "init")
    (Path(repo.working_dir) / "a.py").write_text("def cambio_sin_commitear():\n    return 2\n")

    idx = CodebaseIndexer(ctx, AI)
    idx.client_ollama = _RecordingOllama()
    idx.sync()

    docs = " ".join(idx.collection.get(include=["documents"])["documents"])
    assert "version_commiteada" in docs
    assert "cambio_sin_commitear" not in docs
    assert idx._get_last_indexed_commit() == head


def test_commit_arriving_during_sync_is_not_marked_as_indexed(tmp_path):
    repo, ctx = _repo_and_ctx(tmp_path)
    first = _commit(repo, "a.py", "def a():\n    return 1\n", "init")
    idx = CodebaseIndexer(ctx, AI)
    idx.client_ollama = _RecordingOllama()

    original = idx._index_file
    late = {}

    def index_and_commit_meanwhile(fp, commit_hash=None):
        if not late:  # llega un commit nuevo mientras se indexa
            late["sha"] = _commit(repo, "b.py", "def b():\n    return 2\n", "llega durante el sync")
        return original(fp, commit_hash=commit_hash)

    idx._index_file = index_and_commit_meanwhile
    idx.sync()
    assert idx._get_last_indexed_commit() == first  # no el que llegó durante el sync

    idx._index_file = original
    idx.sync()  # el siguiente sync sí procesa b.py
    files = {m["file_path"] for m in idx.collection.get(include=["metadatas"])["metadatas"]}
    assert "b.py" in files
    assert idx._get_last_indexed_commit() == late["sha"]


# ------------------------------------------------------------------- 1c ----

def test_process_sees_index_written_by_another_process(tmp_path):
    chroma_dir = str(tmp_path / "data" / "chroma_db")
    coll = get_chroma_client(chroma_dir).get_or_create_collection("codebase_index", metadata={"hnsw:space": "cosine"})
    coll.add(ids=["a0", "a1"], embeddings=[[1.0, 0.0, 0.0], [0.9, 0.1, 0.0]], documents=["a", "a"])
    coll.query(query_embeddings=[[1.0, 0.0, 0.0]], n_results=1)  # carga el índice en memoria

    writer = textwrap.dedent(f"""
        import sys; sys.path.insert(0, {SRC!r})
        from pathlib import Path
        from core.engine.chroma_utils import get_chroma_client, bump_index_version
        c = get_chroma_client({chroma_dir!r}).get_or_create_collection("codebase_index", metadata={{"hnsw:space": "cosine"}})
        c.add(ids=["nuevo"], embeddings=[[0.0, 0.0, 1.0]], documents=["b"])
        bump_index_version(Path({chroma_dir!r}).parent)
    """)
    subprocess.run([sys.executable, "-c", writer], check=True, capture_output=True)

    fresh = get_chroma_client(chroma_dir).get_collection("codebase_index")
    found = fresh.query(query_embeddings=[[0.0, 0.0, 1.0]], n_results=1)["ids"][0]
    assert found == ["nuevo"]


def test_own_writes_do_not_force_reload(tmp_path):
    from chromadb.api.shared_system_client import SharedSystemClient
    chroma_dir = str(tmp_path / "data" / "chroma_db")
    get_chroma_client(chroma_dir)
    system_before = SharedSystemClient._identifier_to_system[chroma_dir]
    bump_index_version(Path(chroma_dir).parent)  # escritura de este mismo proceso
    get_chroma_client(chroma_dir)
    assert SharedSystemClient._identifier_to_system[chroma_dir] is system_before


# ------------------------------------------------------------------- 1d ----

def _docgen_with_chunks(tmp_path, chunks):
    _, ctx = _repo_and_ctx(tmp_path)
    gen = DocGenerator(ctx, AI)
    gen.client_ollama = fake = _RecordingOllama(content="Componentes:\n- `x` — nota")
    gen._get_chunks_for_file = lambda fp: chunks
    return gen, fake


def test_small_file_is_documented_in_a_single_call(tmp_path):
    chunks = [{"code": "def f():\n    pass\n", "chunk_type": "function", "name": "f", "start_line": 1, "end_line": 2}]
    gen, fake = _docgen_with_chunks(tmp_path, chunks)
    doc = gen.generate_doc_for_file("a.py")
    assert len(fake.chats) == 1
    assert "partes" not in doc


def test_large_file_is_documented_completely_in_parts(tmp_path):
    # ~70.000 caracteres (como sessions.py de requests, que antes quedaba al 28%)
    chunks = [{"code": f"def funcion_{i}():\n" + "    x = 1\n" * 350, "chunk_type": "function",
               "name": f"funcion_{i}", "start_line": i * 400, "end_line": i * 400 + 351} for i in range(20)]
    gen, fake = _docgen_with_chunks(tmp_path, chunks)
    doc = gen.generate_doc_for_file("grande.py")

    notes_calls = [c for c in fake.chats if "UNA PARTE" in c["messages"][0]["content"]]
    assert len(notes_calls) >= 2
    sent_code = "".join(c["messages"][1]["content"] for c in notes_calls)
    assert all(f"funcion_{i}" in sent_code for i in range(20))  # cada componente se analizó
    final_prompt = fake.chats[-1]["messages"][1]["content"]
    assert all(f"`funcion_{i}`" in final_prompt for i in range(20))  # y aparece en el índice final
    assert "se analizó completo en" in doc
    index = doc.split("## Índice de componentes", 1)[1]
    assert all(f"`funcion_{i}`" in index for i in range(20))  # índice exacto, no depende del modelo
    for call in fake.chats:
        sent = sum(len(m["content"]) for m in call["messages"])
        assert llm.estimate_tokens("x" * sent) <= llm.prompt_budget_tokens(1536)


def test_components_index_lists_split_chunks_once():
    chunks = [
        {"code": "a", "chunk_type": "function", "name": "grande", "start_line": 1, "end_line": 900,
         "split_of": "x.py::1-900", "split_depth": 1},
        {"code": "b", "chunk_type": "function", "name": "grande", "start_line": 1, "end_line": 900,
         "split_of": "x.py::1-900", "split_depth": 1},
        {"code": "c", "chunk_type": "module_level", "name": "module_level", "start_line": 1, "end_line": 3},
    ]
    index = DocGenerator._components_index(chunks)
    assert index.count("`grande`") == 1
    assert "código a nivel de módulo" in index
