"""Espacio por proyecto y limpieza selectiva: nunca se toca la carpeta de un usuario, y lo borrado se vuelve a crear."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402
from typer.testing import CliRunner  # noqa: E402

import core.cli as cli_module  # noqa: E402
import core.projects as projects_module  # noqa: E402
import core.services as services  # noqa: E402
import core.storage as storage  # noqa: E402
from core.config import AIProviderConfig, AppConfig, ProjectEntry  # noqa: E402

AI = AIProviderConfig(provider="ollama", ollama_host="http://x", embedding_model="e", llm_model="l")


def _write(path: Path, size: int):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)


@pytest.fixture
def env(tmp_path, monkeypatch):
    local = tmp_path / "mi-carpeta"
    _write(local / "a.py", 5000)
    entries = [
        ProjectEntry(id="clonado", name="c", repo_path=str(tmp_path / "data" / "clonado" / "repo"), source_type="git",
                     repo_url="https://github.com/u/r"),
        ProjectEntry(id="local", name="l", repo_path=str(local), source_type="local"),
    ]
    _write(tmp_path / "data" / "clonado" / "repo" / "f.py", 3000)
    _write(tmp_path / "data" / "clonado" / "chroma_db" / "i.bin", 2000)
    _write(tmp_path / "data" / "clonado" / "docs" / "d.md", 1000)
    _write(tmp_path / "data" / "local" / "chroma_db" / "i.bin", 700)
    config = AppConfig(ai_provider=AI, projects=entries)
    for module in (storage, projects_module):
        monkeypatch.setattr(module, "DATA_ROOT", tmp_path / "data")
    monkeypatch.setattr(storage, "load_config", lambda: config)
    monkeypatch.setattr(cli_module, "DATA_ROOT", tmp_path / "data")
    monkeypatch.setattr(storage, "release_chroma_client", lambda p: None)
    return tmp_path


def test_usage_separates_clone_index_and_docs_and_ignores_user_folders(env):
    usage = {u.id: u for u in storage.list_usage()}
    assert (usage["clonado"].clone_bytes, usage["clonado"].index_bytes, usage["clonado"].docs_bytes) == (3000, 2000, 1000)
    assert usage["local"].clone_bytes == 0 and usage["local"].clone_path == "" and usage["local"].index_bytes == 700


def test_cleaning_deletes_only_what_was_asked_and_reports_the_freed_space(env):
    assert storage.clean_project("clonado", clone=True) == 3000
    assert not (env / "data" / "clonado" / "repo").exists() and (env / "data" / "clonado" / "chroma_db" / "i.bin").exists()
    assert storage.clean_project("clonado", index=True, docs=True) == 3000
    assert (env / "data" / "clonado" / "chroma_db").is_dir() and not list((env / "data" / "clonado" / "chroma_db").iterdir())


def test_a_users_own_folder_is_never_deleted(env):
    assert storage.clean_project("local", clone=True, index=True) == 700  # solo el índice: la carpeta es del usuario
    assert (env / "mi-carpeta" / "a.py").exists()


def test_unknown_project_is_reported(env):
    with pytest.raises(storage.ProjectNotFoundError):
        storage.clean_project("nope", index=True)


def test_a_deleted_clone_is_cloned_again_on_demand(env, monkeypatch):
    storage.clean_project("clonado", clone=True)
    entry = storage.load_config().projects[0]
    cloned = []
    monkeypatch.setattr(services, "get_project_token", lambda pid: None)
    monkeypatch.setattr(services.git.Repo, "clone_from", lambda url, path: cloned.append((url, path)))
    assert services.ensure_clone(entry) is True and cloned == [("https://github.com/u/r", entry.repo_path)]
    (env / "data" / "clonado" / "repo").mkdir(parents=True)
    assert services.ensure_clone(entry) is False  # ya existe: no se clona


def test_cli_space_and_clean(env, monkeypatch):
    runner = CliRunner()
    monkeypatch.setattr(cli_module, "_resolve_or_exit", lambda pid: None)
    out = runner.invoke(cli_module.app, ["space"])
    assert out.exit_code == 0 and "clonado" in out.output and "local" in out.output
    assert runner.invoke(cli_module.app, ["clean", "clonado"]).exit_code == 1  # sin indicar qué borrar
    out = runner.invoke(cli_module.app, ["clean", "clonado", "--docs", "--yes"])
    assert out.exit_code == 0 and not (env / "data" / "clonado" / "docs" / "d.md").exists()


def test_api_storage_and_clean(env, monkeypatch):
    from fastapi.testclient import TestClient
    import core.api as api
    client = TestClient(api.app)
    out = client.get("/system/storage").json()
    assert out["total_bytes"] == 3000 + 2000 + 1000 + 700 and out["projects"][0]["managed_clone"] is True
    assert client.post("/projects/clonado/clean", json={}).status_code == 400
    assert client.post("/projects/nope/clean", json={"index": True}).status_code == 404
    assert client.post("/projects/clonado/clean", json={"clone": True}).json() == {"freed_bytes": 3000}
