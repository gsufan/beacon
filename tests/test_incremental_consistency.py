"""Consistencia del índice y la documentación incrementales frente a casos
borde de git (renames, cambios en staging, historia reescrita) y al orden
sync/docs. Usan un repo git y un ChromaDB temporales; Ollama se reemplaza
por un cliente falso, así que no hace falta tenerlo corriendo."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import git  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import core.api as api  # noqa: E402
from core.config import AIProviderConfig  # noqa: E402
from core.engine.doc_generator import DocGenerator, IndexOutOfDateError  # noqa: E402
from core.engine.git_watcher import GitWatcher  # noqa: E402
from core.engine.indexer import CodebaseIndexer  # noqa: E402
from core.projects import ProjectContext  # noqa: E402

AI = AIProviderConfig(provider="ollama", ollama_host="http://127.0.0.1:9",
                      embedding_model="nomic-embed-text", llm_model="llama3:8b")


class _FakeOllama:
    def embed(self, input, **kwargs):
        return {"embeddings": [[0.1, 0.2, 0.3] for _ in input]}

    def chat(self, **kwargs):
        return {"message": {"content": "## Propósito\nDoc de prueba."}}


def _commit(repo, files: dict, message: str):
    root = Path(repo.working_dir)
    for rel, content in files.items():
        path = root / rel
        if content is None:
            repo.index.remove([rel], working_tree=True)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        repo.index.add([rel])
    repo.index.commit(message)


def _setup(tmp_path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    repo = git.Repo.init(repo_dir)
    ctx = ProjectContext(id="t", name="t", repo_path=str(repo_dir),
                         chroma_dir=str(tmp_path / "chroma"), docs_dir=str(tmp_path / "docs"))
    return repo, ctx


def _indexer(ctx):
    idx = CodebaseIndexer(ctx, AI)
    idx.client_ollama = _FakeOllama()
    return idx


def _indexed_files(idx):
    return {m["file_path"] for m in idx.collection.get(include=["metadatas"])["metadatas"]}


# ------------------------------------------------------------ git_watcher ----

def test_rename_to_non_indexable_extension_counts_as_deleted(tmp_path):
    repo, ctx = _setup(tmp_path)
    _commit(repo, {"a.py": "def f():\n    return 1\n"}, "init")
    base = repo.head.commit.hexsha
    repo.git.mv("a.py", "a.txt")
    repo.index.commit("rename")

    changes = GitWatcher(ctx.repo_path).get_changes_since(base)
    assert changes.deleted == ["a.py"]
    assert changes.renamed == []


def test_uncommitted_includes_staged_new_files(tmp_path):
    repo, ctx = _setup(tmp_path)
    _commit(repo, {"a.py": "x = 1\n"}, "init")
    (Path(ctx.repo_path) / "nuevo.py").write_text("y = 2\n")
    repo.index.add(["nuevo.py"])  # en staging, sin commit

    changes = GitWatcher(ctx.repo_path).get_uncommitted_changes()
    assert "nuevo.py" in changes.added


# ---------------------------------------------------------------- indexer ----

def test_full_rescan_purges_chunks_of_files_that_no_longer_exist(tmp_path):
    repo, ctx = _setup(tmp_path)
    _commit(repo, {"a.py": "def a():\n    return 1\n", "b.py": "def b():\n    return 2\n"}, "init")
    idx = _indexer(ctx)
    idx.sync()
    assert _indexed_files(idx) == {"a.py", "b.py"}

    # Simula historia reescrita: el commit guardado ya no existe y b.py desapareció
    _commit(repo, {"b.py": None}, "borra b")
    idx._save_last_indexed_commit("0" * 40)
    idx.sync()
    assert _indexed_files(idx) == {"a.py"}


def test_resync_of_added_file_does_not_leave_stale_chunks(tmp_path):
    repo, ctx = _setup(tmp_path)
    _commit(repo, {"a.py": "x = 1\n"}, "init")
    idx = _indexer(ctx)
    idx.sync()

    nuevo = Path(ctx.repo_path) / "nuevo.py"
    nuevo.write_text("def viejo():\n    return 1\n")
    idx.sync(include_uncommitted=True)
    nuevo.write_text("\n\n\ndef nuevo():\n    return 2\n")  # cambia el rango de líneas
    _commit(repo, {"nuevo.py": nuevo.read_text()}, "agrega nuevo.py")
    idx.sync()

    names = {m["name"] for m in idx.collection.get(where={"file_path": "nuevo.py"}, include=["metadatas"])["metadatas"]}
    assert names == {"nuevo"}


# ------------------------------------------------------------ doc_generator ----

def test_docs_refuse_to_run_when_index_is_behind_head(tmp_path):
    repo, ctx = _setup(tmp_path)
    _commit(repo, {"a.py": "def a():\n    return 1\n"}, "init")
    idx = _indexer(ctx)
    idx.sync()
    _commit(repo, {"a.py": "def a():\n    return 2\n"}, "cambio sin reindexar")

    gen = DocGenerator(ctx, AI)
    gen.client_ollama = _FakeOllama()
    with pytest.raises(IndexOutOfDateError):
        gen.sync()
    assert gen._get_last_documented_commit() is None


def test_docs_full_rescan_removes_orphan_docs(tmp_path):
    repo, ctx = _setup(tmp_path)
    _commit(repo, {"a.py": "def a():\n    return 1\n", "b.py": "def b():\n    return 2\n"}, "init")
    idx = _indexer(ctx)
    idx.sync()
    gen = DocGenerator(ctx, AI)
    gen.client_ollama = _FakeOllama()
    gen.sync()
    assert (Path(ctx.docs_dir) / "b.py.md").exists()

    _commit(repo, {"b.py": None}, "borra b")
    idx._save_last_indexed_commit("0" * 40)
    idx.sync()
    gen._save_last_documented_commit("0" * 40)  # historia reescrita también para las docs
    gen.sync()
    assert not (Path(ctx.docs_dir) / "b.py.md").exists()
    assert (Path(ctx.docs_dir) / "a.py.md").exists()


# ------------------------------------------------------------ race en /sync ----

def test_second_sync_request_is_rejected_while_first_is_claimed(monkeypatch):
    monkeypatch.setattr(api, "get_project", lambda pid: object())
    monkeypatch.setattr(api, "_run_sync", lambda pid: None)  # la tarea "no termina": el claim sigue vigente
    client = TestClient(api.app)
    try:
        assert client.post("/projects/demo-race/sync").status_code == 202
        assert client.post("/projects/demo-race/sync").status_code == 409
    finally:
        api._sync_status.pop("demo-race", None)


def test_auto_watch_releases_claim_when_sync_raises(monkeypatch):
    from core.config import AppConfig, ProjectEntry

    entry = ProjectEntry(id="demo-watch", name="d", repo_path=".", auto_watch=True)
    monkeypatch.setattr(api, "load_config", lambda: AppConfig(ai_provider=AI, projects=[entry]))

    def boom(pid):
        raise RuntimeError("falla")

    monkeypatch.setattr(api, "_run_sync", boom)
    try:
        api._auto_watch_tick()
        assert api._sync_status["demo-watch"]["status"] == "error"
    finally:
        api._sync_status.pop("demo-watch", None)
