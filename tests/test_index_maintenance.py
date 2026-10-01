"""Mantenimiento del índice vectorial (2026-10): reconstrucción limpia,
carpetas huérfanas de ChromaDB y consultas sobre un grafo HNSW con muchos
elementos borrados. Ollama se reemplaza por un cliente falso."""

import sqlite3
import sys
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import git  # noqa: E402
import pytest  # noqa: E402

from core.config import AIProviderConfig  # noqa: E402
from core.engine.chroma_utils import remove_orphan_segments  # noqa: E402
from core.engine.indexer import CodebaseIndexer  # noqa: E402
from core.engine.rag_engine import RAGEngine  # noqa: E402
from core.projects import ProjectContext  # noqa: E402

AI = AIProviderConfig(provider="ollama", ollama_host="http://127.0.0.1:9",
                      embedding_model="qwen3-embedding:0.6b", llm_model="l")
ORPHAN = "00000000-1111-2222-3333-444444444444"


class _FakeEmbed:
    def embed(self, model, input, **kwargs):
        return {"embeddings": [[0.1, 0.2, 0.3] for _ in input]}


def _project(tmp_path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    repo = git.Repo.init(repo_dir)
    (repo_dir / "a.py").write_text("def a():\n    return 1\n\n\ndef b():\n    return 2\n")
    repo.index.add(["a.py"])
    repo.index.commit("init")
    return ProjectContext(id="t", name="t", repo_path=str(repo_dir),
                          chroma_dir=str(tmp_path / "data" / "chroma_db"), docs_dir=str(tmp_path / "data" / "docs"))


def _indexer(ctx):
    idx = CodebaseIndexer(ctx, AI)
    idx.client_ollama = _FakeEmbed()
    return idx


def test_full_rebuild_recreates_the_collection_instead_of_deleting_one_by_one(tmp_path):
    ctx = _project(tmp_path)
    idx = _indexer(ctx)
    idx.sync()
    first_id = idx.collection.id
    result = idx.sync(full=True)
    # Colección nueva: el grafo HNSW parte sin elementos marcados como borrados.
    assert idx.collection.id != first_id
    assert result["chunks_purgados"] == result["chunks_insertados"] == idx.collection.count() > 0


def test_orphan_segment_folders_are_removed_and_live_ones_kept(tmp_path):
    ctx = _project(tmp_path)
    _indexer(ctx).sync()
    chroma_dir = Path(ctx.chroma_dir)
    orphan = chroma_dir / ORPHAN
    orphan.mkdir()
    (orphan / "data_level0.bin").write_bytes(b"x")
    unrelated = chroma_dir / "no-es-un-segmento"
    unrelated.mkdir()

    assert remove_orphan_segments(str(chroma_dir)) == 1
    assert not orphan.exists()
    assert unrelated.exists()  # solo se tocan carpetas con nombre de segmento
    with closing(sqlite3.connect(chroma_dir / "chroma.sqlite3")) as conn:
        live = {row[0] for row in conn.execute("SELECT id FROM segments")}
    for segment in live:  # los segmentos en uso siguen intactos (si tienen carpeta)
        assert not (chroma_dir / segment).exists() or any((chroma_dir / segment).iterdir())


def test_orphan_cleanup_without_catalog_does_nothing(tmp_path):
    (tmp_path / ORPHAN).mkdir()
    assert remove_orphan_segments(str(tmp_path)) == 0
    assert (tmp_path / ORPHAN).exists()


class _FlakyCollection:
    """Como hnswlib con muchos borrados: no reúne más de `max_ok` vecinos."""
    def __init__(self, max_ok):
        self.max_ok, self.requested = max_ok, []

    def count(self):
        return 100

    def query(self, query_embeddings, n_results):
        self.requested.append(n_results)
        if n_results > self.max_ok:
            raise RuntimeError("Cannot return the results in a contigious 2D array. Probably ef or M is too small")
        rows = range(n_results)
        return {"ids": [[f"id{i}" for i in rows]], "documents": [[f"code {i}" for i in rows]],
                "metadatas": [[{"file_path": f"f{i}.py", "name": f"f{i}"} for i in rows]],
                "distances": [[0.1 + i / 100 for i in rows]]}


def test_query_retries_with_fewer_candidates_instead_of_failing(tmp_path):
    ctx = _project(tmp_path)
    _indexer(ctx).sync()
    engine = RAGEngine(ctx, AI)
    engine.client_ollama = _FakeEmbed()
    engine.collection = _FlakyCollection(max_ok=12)
    chunks = engine.retrieve("¿qué hace a?", top_k=5)
    assert len(chunks) == 5
    assert engine.collection.requested == [20, 10]


def test_other_chroma_errors_are_not_hidden(tmp_path):
    ctx = _project(tmp_path)
    _indexer(ctx).sync()
    engine = RAGEngine(ctx, AI)
    engine.client_ollama = _FakeEmbed()

    class Broken(_FlakyCollection):
        def query(self, query_embeddings, n_results):
            raise RuntimeError("disk I/O error")

    engine.collection = Broken(0)
    with pytest.raises(RuntimeError, match="disk I/O"):
        engine.retrieve("¿qué hace a?")


# ------------------------------------------ reconstrucción sin interrupción ----

def test_queries_keep_working_while_the_index_is_rebuilt(tmp_path):
    # Medido con tools/stress_test.py: antes, una consulta durante un
    # `sync --full` de otra consola respondía error 500.
    ctx = _project(tmp_path)
    _indexer(ctx).sync()
    engine = RAGEngine(ctx, AI)  # como el servidor: abierta antes de reconstruir
    engine.client_ollama = _FakeEmbed()
    seen_during = []

    def query_midway(current, total, path):
        seen_during.append(len(engine.retrieve("¿qué hace a?", top_k=5)))

    _indexer(ctx).sync(full=True, on_progress=query_midway)
    assert seen_during and all(n > 0 for n in seen_during)  # el índice anterior seguía disponible
    assert len(engine.retrieve("¿qué hace a?", top_k=5)) > 0  # y después ve el nuevo


def test_a_failed_rebuild_leaves_the_previous_index_intact(tmp_path):
    ctx = _project(tmp_path)
    idx = _indexer(ctx)
    idx.sync()
    before = idx.collection.count()

    def explode(*args):
        raise RuntimeError("Ollama se cayó a mitad de la reconstrucción")

    with pytest.raises(RuntimeError):
        _indexer(ctx).sync(full=True, on_progress=explode)
    fresh = _indexer(ctx)
    assert fresh.collection.count() == before
    assert "codebase_index__rebuild" not in [c.name for c in fresh.client.list_collections()]


# ------------------------------------------------- borrado de un proyecto ----

def _registered(monkeypatch, tmp_path):
    import core.services as services
    from core.config import AppConfig, ProjectEntry

    data_root = tmp_path / "data"
    project_dir = data_root / "demo"
    ctx = _project(tmp_path)
    ctx = ProjectContext(id="demo", name="demo", repo_path=ctx.repo_path,
                         chroma_dir=str(project_dir / "chroma_db"), docs_dir=str(project_dir / "docs"))
    removed = []
    monkeypatch.setattr(services, "DATA_ROOT", data_root)
    monkeypatch.setattr(services, "remove_project", lambda pid: removed.append(pid) or True)
    monkeypatch.setattr(services, "delete_project_token", lambda pid: None)
    return services, ctx, project_dir, removed


def test_purge_works_after_the_index_was_queried_in_this_process(monkeypatch, tmp_path):
    # En Windows, el índice abierto impedía borrar la carpeta (WinError 32).
    services, ctx, project_dir, removed = _registered(monkeypatch, tmp_path)
    _indexer(ctx).sync()
    engine = RAGEngine(ctx, AI)
    engine.client_ollama = _FakeEmbed()
    engine.retrieve("¿qué hace a?")
    del engine

    services.unregister_project("demo", purge_data=True)
    assert removed == ["demo"] and not project_dir.exists()


def test_purge_of_data_in_use_changes_nothing(monkeypatch, tmp_path):
    from core.project_lock import ProjectBusyError

    services, ctx, project_dir, removed = _registered(monkeypatch, tmp_path)
    _indexer(ctx).sync()
    original_rename = Path.rename

    def locked(self, target):
        if self == project_dir:
            raise PermissionError("[WinError 32] archivo en uso")
        return original_rename(self, target)

    monkeypatch.setattr(Path, "rename", locked)
    with pytest.raises(ProjectBusyError, match="no se borró nada"):
        services.unregister_project("demo", purge_data=True)
    assert removed == []  # sigue registrado
    assert (project_dir / "chroma_db").exists()  # y con sus datos
