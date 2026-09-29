"""Tests de 'beacon add'/'edit'/'remove' (CRUD de proyectos) con datos
temporales — no tocan config.yaml real ni requieren Ollama."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import git  # noqa: E402
from typer.testing import CliRunner  # noqa: E402

import core.cli as cli_module  # noqa: E402
import core.services as services  # noqa: E402
from core.config import ProjectAlreadyExistsError, ProjectEntry  # noqa: E402

runner = CliRunner()


def _git_repo(tmp_path, name="repo") -> str:
    repo_dir = tmp_path / name
    repo_dir.mkdir()
    git.Repo.init(repo_dir)
    return str(repo_dir)


# ---------------------------------------------------------------- add ----

def test_add_requires_repo_path_or_url():
    result = runner.invoke(cli_module.app, ["add", "demo"])
    assert result.exit_code == 1


def test_add_rejects_both_repo_path_and_url(tmp_path):
    repo_path = _git_repo(tmp_path)
    result = runner.invoke(cli_module.app, ["add", "demo", "--repo-path", repo_path, "--url", "https://example.com/x.git"])
    assert result.exit_code == 1


def test_add_rejects_nonexistent_repo_path():
    result = runner.invoke(cli_module.app, ["add", "demo", "--repo-path", "/no/existe/esta/ruta"])
    assert result.exit_code == 1


def test_add_rejects_non_git_repo_path(tmp_path):
    not_a_repo = tmp_path / "not-a-repo"
    not_a_repo.mkdir()
    result = runner.invoke(cli_module.app, ["add", "demo", "--repo-path", str(not_a_repo)])
    assert result.exit_code == 1


def test_add_rejects_dangerous_url_scheme():
    result = runner.invoke(cli_module.app, ["add", "demo", "--url", "ext::sh -c 'echo pwned'"])
    assert result.exit_code == 1


def test_add_local_repo_success(monkeypatch, tmp_path):
    repo_path = _git_repo(tmp_path)
    added = {}
    monkeypatch.setattr(services, "add_project", lambda entry: added.setdefault("entry", entry) or entry)
    monkeypatch.setattr(services, "ensure_project_dirs", lambda project_id: None)

    result = runner.invoke(cli_module.app, ["add", "demo", "--repo-path", repo_path, "--name", "Demo"])
    assert result.exit_code == 0, result.output
    assert added["entry"].id == "demo"
    assert added["entry"].name == "Demo"
    assert added["entry"].repo_path == repo_path
    assert added["entry"].source_type == "local"


def test_add_defaults_name_to_id(monkeypatch, tmp_path):
    repo_path = _git_repo(tmp_path)
    added = {}
    monkeypatch.setattr(services, "add_project", lambda entry: added.setdefault("entry", entry) or entry)
    monkeypatch.setattr(services, "ensure_project_dirs", lambda project_id: None)

    result = runner.invoke(cli_module.app, ["add", "demo", "--repo-path", repo_path])
    assert result.exit_code == 0, result.output
    assert added["entry"].name == "demo"


def test_add_duplicate_id_fails(monkeypatch, tmp_path):
    repo_path = _git_repo(tmp_path)

    def fake_add_project(entry):
        raise ProjectAlreadyExistsError(f"El proyecto '{entry.id}' ya está registrado.")

    monkeypatch.setattr(services, "add_project", fake_add_project)
    result = runner.invoke(cli_module.app, ["add", "demo", "--repo-path", repo_path])
    assert result.exit_code == 1


# --------------------------------------------------------------- edit ----

def test_edit_requires_at_least_one_field():
    result = runner.invoke(cli_module.app, ["edit", "demo"])
    assert result.exit_code == 1


def test_edit_rejects_nonexistent_repo_path():
    result = runner.invoke(cli_module.app, ["edit", "demo", "--repo-path", "/no/existe/esta/ruta"])
    assert result.exit_code == 1


def test_edit_unknown_project_fails(monkeypatch):
    monkeypatch.setattr(cli_module, "update_project", lambda *a, **k: False)
    result = runner.invoke(cli_module.app, ["edit", "no-existe", "--name", "Nuevo nombre"])
    assert result.exit_code == 1


def test_edit_updates_name(monkeypatch):
    calls = {}
    monkeypatch.setattr(cli_module, "update_project", lambda pid, name=None, repo_path=None: calls.setdefault("update", (pid, name, repo_path)) or True)
    result = runner.invoke(cli_module.app, ["edit", "demo", "--name", "Nuevo nombre"])
    assert result.exit_code == 0, result.output
    assert calls["update"] == ("demo", "Nuevo nombre", None)


def test_edit_toggles_auto_watch(monkeypatch):
    calls = {}

    def fake_set_auto_watch(pid, enabled):
        calls["watch"] = (pid, enabled)
        return True

    monkeypatch.setattr(cli_module, "set_auto_watch", fake_set_auto_watch)
    result = runner.invoke(cli_module.app, ["edit", "demo", "--auto-watch"])
    assert result.exit_code == 0, result.output
    assert calls["watch"] == ("demo", True)

    result = runner.invoke(cli_module.app, ["edit", "demo", "--no-auto-watch"])
    assert result.exit_code == 0, result.output
    assert calls["watch"] == ("demo", False)


# ------------------------------------------------------------- remove ----

def test_remove_unknown_project_fails(monkeypatch):
    monkeypatch.setattr(services, "remove_project", lambda pid: False)
    result = runner.invoke(cli_module.app, ["remove", "no-existe"])
    assert result.exit_code == 1


def test_remove_without_purge_keeps_data_dir(monkeypatch, tmp_path):
    data_root = tmp_path / "data"
    project_dir = data_root / "demo"
    project_dir.mkdir(parents=True)
    (project_dir / "marker.txt").write_text("no me deberian borrar")

    monkeypatch.setattr(services, "remove_project", lambda pid: True)
    monkeypatch.setattr(services, "delete_project_token", lambda pid: None)
    monkeypatch.setattr(services, "DATA_ROOT", data_root)

    result = runner.invoke(cli_module.app, ["remove", "demo"])
    assert result.exit_code == 0, result.output
    assert project_dir.exists()


def test_remove_with_purge_deletes_data_dir(monkeypatch, tmp_path):
    data_root = tmp_path / "data"
    project_dir = data_root / "demo"
    project_dir.mkdir(parents=True)
    (project_dir / "marker.txt").write_text("esto si se deberia borrar")

    monkeypatch.setattr(services, "remove_project", lambda pid: True)
    monkeypatch.setattr(services, "delete_project_token", lambda pid: None)
    monkeypatch.setattr(services, "DATA_ROOT", data_root)

    result = runner.invoke(cli_module.app, ["remove", "demo", "--purge-data"])
    assert result.exit_code == 0, result.output
    assert not project_dir.exists()
