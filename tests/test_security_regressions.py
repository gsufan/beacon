"""Regresiones de la revisión de seguridad/integridad (path traversal, ids
peligrosos, índice marcado como al día con Ollama caído)."""

import json
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import git  # noqa: E402
import ollama  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from typer.testing import CliRunner  # noqa: E402

import core.api as api  # noqa: E402
import core.cli as cli_module  # noqa: E402
from core.config import AIProviderConfig, InvalidProjectIdError, validate_project_id  # noqa: E402
from core.engine.indexer import CodebaseIndexer  # noqa: E402
from core.projects import ProjectContext, safe_project_dir  # noqa: E402

client = TestClient(api.app)
runner = CliRunner()


# ------------------------------------------------ SPA fallback traversal ----

def test_frontend_file_resolves_inside_dist(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "assets" / "app.js").write_text("ok")
    assert api._resolve_frontend_file("assets/app.js", dist) == (dist / "assets" / "app.js").resolve()


@pytest.mark.parametrize("path", ["../secret.yaml", "../../secret.yaml", "assets/../../secret.yaml"])
def test_frontend_file_rejects_traversal(tmp_path, path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (tmp_path / "secret.yaml").write_text("token: ghp_x")
    assert api._resolve_frontend_file(path, dist) is None


def test_frontend_file_missing_or_empty_returns_none(tmp_path):
    assert api._resolve_frontend_file("", tmp_path) is None
    assert api._resolve_frontend_file("no-existe.js", tmp_path) is None


# ------------------------------------------------------- ids de proyecto ----

@pytest.mark.parametrize("bad_id", ["", ".", "..", "../x", "a/b", "a\\b", "C:/Users", "-x", "x" * 65])
def test_validate_project_id_rejects(bad_id):
    with pytest.raises(InvalidProjectIdError):
        validate_project_id(bad_id)


@pytest.mark.parametrize("ok_id", ["requests", "micro-services_demo", "v1.2", "A1"])
def test_validate_project_id_accepts(ok_id):
    validate_project_id(ok_id)


def test_api_create_project_rejects_dotdot_id(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git.Repo.init(repo)
    r = client.post("/projects", json={"id": "..", "name": "x", "source_type": "local", "repo_path": str(repo)})
    assert r.status_code == 400


def test_safe_project_dir_rejects_escape(tmp_path):
    with pytest.raises(ValueError):
        safe_project_dir("..", tmp_path)
    assert safe_project_dir("demo", tmp_path) == (tmp_path / "demo").resolve()


def test_cli_add_rejects_dotdot_id(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git.Repo.init(repo)
    result = runner.invoke(cli_module.app, ["add", "..", "--repo-path", str(repo)])
    assert result.exit_code == 1


def test_cli_import_rejects_malicious_manifest_id(monkeypatch, tmp_path):
    evil_zip = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil_zip, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"id": "../escaped", "name": "x", "repo_path": "/tmp/x"}))
        zf.writestr("data/chroma_db/file.bin", "x")
    data_root = tmp_path / "data"
    monkeypatch.setattr(cli_module, "DATA_ROOT", data_root)

    result = runner.invoke(cli_module.app, ["import", str(evil_zip)])
    assert result.exit_code == 1
    assert not (tmp_path / "escaped").exists()


# ------------------------------------------- Ollama caído durante el sync ----

class _FailingOllama:
    def __init__(self, exc):
        self.exc = exc

    def embed(self, **kwargs):
        raise self.exc


def _indexer_for_small_repo(tmp_path) -> CodebaseIndexer:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    repo = git.Repo.init(repo_dir)
    (repo_dir / "a.py").write_text("def hola():\n    return 1\n")
    repo.index.add(["a.py"])
    repo.index.commit("init")
    ctx = ProjectContext(id="t", name="t", repo_path=str(repo_dir),
                         chroma_dir=str(tmp_path / "chroma"), docs_dir=str(tmp_path / "docs"))
    ai = AIProviderConfig(provider="ollama", ollama_host="http://127.0.0.1:9",
                          embedding_model="nomic-embed-text", llm_model="llama3:8b")
    return CodebaseIndexer(ctx, ai)


@pytest.mark.parametrize("exc", [
    ConnectionError("Failed to connect to Ollama"),
    ollama.ResponseError("model 'nomic-embed-text' not found", 404),
])
def test_sync_does_not_mark_commit_indexed_when_embeddings_fail(tmp_path, exc):
    indexer = _indexer_for_small_repo(tmp_path)
    indexer.client_ollama = _FailingOllama(exc)

    with pytest.raises(type(exc)):
        indexer.sync()
    assert indexer._get_last_indexed_commit() is None


def test_sync_still_skips_chunk_rejected_for_length(tmp_path):
    """Un rechazo por contenido (no 404) sigue siendo tolerado: se omite ese chunk."""
    indexer = _indexer_for_small_repo(tmp_path)
    indexer.client_ollama = _FailingOllama(ollama.ResponseError("input length exceeds context length", 500))

    result = indexer.sync()
    assert result["status"] == "ok"


def test_chromadb_is_only_used_embedded():
    # Los CVE de chromadb 0.5.x publicados en 2026 (CVE-2026-45830/45831/45833)
    # afectan su modo servidor (API HTTP, permisos por tenant). Beacon usa la
    # base embebida en el proceso; esta prueba falla si alguien introduce un
    # cliente o un servidor HTTP de ChromaDB sin revisar esos riesgos.
    src = Path(__file__).resolve().parents[1] / "src"
    offenders = [str(p) for p in src.rglob("*.py")
                 if any(t in p.read_text(encoding="utf-8") for t in ("HttpClient", "chromadb.Client(", "chroma run"))]
    assert offenders == []
