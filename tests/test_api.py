"""Tests de los endpoints nuevos de core.api con FastAPI TestClient.

Usa el config.yaml real del repo para los casos de solo lectura / 404
(no mutan estado). Los casos que sí escribirían en config.yaml o
dispararían indexado real se prueban con monkeypatch para no tocar
datos reales del proyecto.
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import core.api as api  # noqa: E402
from core.projects import ProjectNotFoundError  # noqa: E402

client = TestClient(api.app)

UNKNOWN_PROJECT = "does-not-exist-project"


def test_health_unknown_project_404():
    resp = client.get(f"/projects/{UNKNOWN_PROJECT}/health")
    assert resp.status_code == 404


def test_docs_tree_unknown_project_404():
    resp = client.get(f"/projects/{UNKNOWN_PROJECT}/docs/tree")
    assert resp.status_code == 404


def test_docs_missing_file_404():
    resp = client.get(f"/projects/{UNKNOWN_PROJECT}/docs", params={"file_path": "a.py"})
    assert resp.status_code == 404


def test_sync_unknown_project_404():
    resp = client.post(f"/projects/{UNKNOWN_PROJECT}/sync")
    assert resp.status_code == 404


def test_sync_status_unknown_project_returns_idle():
    resp = client.get(f"/projects/{UNKNOWN_PROJECT}/sync-status")
    assert resp.status_code == 200
    assert resp.json()["status"] == "idle"


def test_delete_unknown_project_404():
    resp = client.delete(f"/projects/{UNKNOWN_PROJECT}")
    assert resp.status_code == 404


def test_create_project_invalid_path_400():
    resp = client.post("/projects", json={
        "id": "temp-test-project", "name": "Temp", "repo_path": "C:/no/existe/de/verdad",
    })
    assert resp.status_code == 400


def test_create_project_non_git_repo_400():
    with tempfile.TemporaryDirectory() as tmp:
        resp = client.post("/projects", json={
            "id": "temp-test-project", "name": "Temp", "repo_path": tmp,
        })
        assert resp.status_code == 400


def test_create_project_duplicate_id_409():
    cfg = api.load_config()
    existing_id = cfg.projects[0].id
    repo_root = str(Path(__file__).resolve().parents[1])  # el propio repo, es un git repo válido
    resp = client.post("/projects", json={
        "id": existing_id, "name": "Duplicado", "repo_path": repo_root,
    })
    assert resp.status_code == 409


def test_get_config_returns_ai_provider_and_projects():
    resp = client.get("/config")
    assert resp.status_code == 200
    body = resp.json()
    assert "ai_provider" in body and "projects" in body
    assert "provider" in body["ai_provider"]


def test_put_ai_provider_wiring(monkeypatch):
    calls = {}

    def fake_update_ai_provider(**kwargs):
        calls.update(kwargs)
        return type("Cfg", (), kwargs)()

    monkeypatch.setattr(api, "update_ai_provider", fake_update_ai_provider)
    api._get_engine.cache_clear()

    resp = client.put("/config/ai-provider", json={
        "provider": "ollama", "ollama_host": "http://localhost:11434",
        "embedding_model": "nomic-embed-text", "llm_model": "llama3:8b",
    })
    assert resp.status_code == 200
    assert calls["provider"] == "ollama"


def test_query_unknown_project_404():
    resp = client.post(f"/projects/{UNKNOWN_PROJECT}/query", json={"question": "que hace este repo?"})
    assert resp.status_code == 404


def test_system_providers():
    resp = client.get("/system/providers")
    assert resp.status_code == 200
    ids = [p["id"] for p in resp.json()["providers"]]
    assert "ollama" in ids


def test_system_available_models_bad_host():
    resp = client.get("/system/available-models", params={"ollama_host": "http://localhost:1"})
    assert resp.status_code == 502


def test_system_browse_dirs_invalid_path():
    resp = client.get("/system/browse-dirs", params={"path": "C:/no/existe/de/verdad"})
    assert resp.status_code == 400


def test_system_browse_dirs_defaults_to_home(monkeypatch):
    resp = client.get("/system/browse-dirs")
    assert resp.status_code == 200
    body = resp.json()
    assert "path" in body and "directories" in body


def test_auto_watch_unknown_project_404():
    resp = client.put("/projects/does-not-exist/auto-watch", json={"enabled": True})
    assert resp.status_code == 404


def test_auto_watch_wiring(monkeypatch):
    calls = {}

    def fake_set_auto_watch(project_id, enabled):
        calls["project_id"] = project_id
        calls["enabled"] = enabled
        return True

    monkeypatch.setattr(api, "set_auto_watch", fake_set_auto_watch)
    resp = client.put("/projects/any-id/auto-watch", json={"enabled": True})
    assert resp.status_code == 200
    assert calls == {"project_id": "any-id", "enabled": True}


def test_create_project_git_missing_url_400():
    resp = client.post("/projects", json={
        "id": "temp-git-project", "name": "Temp", "source_type": "git",
    })
    assert resp.status_code == 400
