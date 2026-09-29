"""Capa de servicios (core/services.py): los flujos compartidos por la CLI,
la API y el watcher. Sin red ni Ollama: el clonado se reemplaza por uno falso."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from typer.testing import CliRunner  # noqa: E402

import core.api as api  # noqa: E402
import core.cli as cli_module  # noqa: E402
import core.services as services  # noqa: E402
from core.config import AIProviderConfig, AppConfig, ProjectAlreadyExistsError, ProjectEntry  # noqa: E402
from core.projects import ProjectNotFoundError  # noqa: E402

AI = AIProviderConfig(provider="ollama", ollama_host="http://x", embedding_model="e", llm_model="l")


class _FakeClone:
    """Registra la URL usada para clonar y la URL que queda en origin."""
    def __init__(self):
        self.clone_url = None
        self.origin_url = None
        outer = self

        class Origin:
            def set_url(self, url):
                outer.origin_url = url

        class Remotes:
            origin = Origin()

        class Repo:
            remotes = Remotes()

        self.repo = Repo()

    def clone_from(self, url, path):
        self.clone_url = url
        return self.repo


def test_duplicate_id_is_rejected_before_cloning(monkeypatch):
    existing = ProjectEntry(id="demo", name="d", repo_path=".")
    monkeypatch.setattr(services, "load_config", lambda: AppConfig(ai_provider=AI, projects=[existing]))
    fake = _FakeClone()
    monkeypatch.setattr(services.git.Repo, "clone_from", fake.clone_from)
    with pytest.raises(ProjectAlreadyExistsError):
        services.register_project("demo", repo_url="https://github.com/psf/requests.git")
    assert fake.clone_url is None  # no se descargó nada


def test_private_repo_token_is_used_to_clone_but_not_left_in_git_config(monkeypatch, tmp_path):
    fake = _FakeClone()
    saved = {}
    monkeypatch.setattr(services, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(services.git.Repo, "clone_from", fake.clone_from)
    monkeypatch.setattr(services, "add_project", lambda entry: entry)
    monkeypatch.setattr(services, "ensure_project_dirs", lambda pid: None)
    monkeypatch.setattr(services, "set_project_token", lambda pid, token: saved.setdefault(pid, token))

    entry = services.register_project("priv", repo_url="https://github.com/org/privado.git",
                                      auth_token="ghp_secreto", auto_watch=True)

    assert "ghp_secreto" in fake.clone_url
    assert fake.origin_url == "https://github.com/org/privado.git"  # URL limpia en .git/config
    assert saved == {"priv": "ghp_secreto"}  # el token vive en credentials.yaml
    assert entry.source_type == "git" and entry.auto_watch is True


def test_unregister_unknown_project_raises_not_found():
    with pytest.raises(ProjectNotFoundError):
        services.unregister_project("no-existe-en-config")


def test_api_create_project_honors_auto_watch(monkeypatch, tmp_path):
    captured = {}

    def fake_register(project_id, **kwargs):
        captured.update(kwargs, id=project_id)
        return ProjectEntry(id=project_id, name=kwargs["name"], repo_path=kwargs["repo_path"],
                            auto_watch=kwargs["auto_watch"])

    monkeypatch.setattr(services, "register_project", fake_register)
    resp = TestClient(api.app).post("/projects", json={
        "id": "demo", "name": "Demo", "repo_path": str(tmp_path), "auto_watch": True,
    })
    assert resp.status_code == 201, resp.text
    assert captured["auto_watch"] is True and captured["repo_url"] is None


def test_cli_add_private_prompts_for_token_without_echo(monkeypatch):
    captured = {}
    monkeypatch.setattr(services, "register_project",
                        lambda pid, **kw: captured.update(kw) or ProjectEntry(id=pid, name=pid, repo_path="x"))
    result = CliRunner().invoke(cli_module.app, ["add", "priv", "--url", "https://github.com/org/p.git", "--private"],
                                input="ghp_secreto\n")
    assert result.exit_code == 0, result.output
    assert captured["auth_token"] == "ghp_secreto"
    assert "ghp_secreto" not in result.output  # no se muestra al escribirlo
