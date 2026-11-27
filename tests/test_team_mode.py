"""Beacon Local in team mode: sessions and licenses signed by the Hub, Scrum roles per route, audit and issues."""
import sys
import time
from types import SimpleNamespace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import core.api as api  # noqa: E402
from core import team  # noqa: E402
from core.config import ProjectEntry  # noqa: E402
from hub.security import load_or_create_key, public_key_text, sign_token  # noqa: E402

HUB = "http://hub.test"


@pytest.fixture
def hub_key(tmp_path, monkeypatch):
    key = load_or_create_key(tmp_path / "k.pem")
    settings = team.HubSettings(HUB, public_key_text(key))
    monkeypatch.setattr(team, "hub_settings", lambda: settings)
    monkeypatch.setattr(team, "hub_url", lambda: HUB)
    reported = []
    monkeypatch.setattr(team, "report", lambda s, auth, action, project: reported.append((action, project)))
    return SimpleNamespace(key=key, reported=reported)


def _headers(hub, roles=None, admin=False, org=1, license_exp=None):
    key = hub.key
    session = sign_token(key, {"typ": "session", "sub": 5, "org": org, "name": "Diego", "admin": admin,
                               "roles": roles or {}, "exp": int(time.time()) + 600})
    lic = sign_token(key, {"typ": "license", "org": 1, "plan": "trial", "seats": 5,
                           "exp": license_exp or int(time.time()) + 86400})
    return {"Authorization": f"Bearer {session}", "X-Beacon-License": lic}


@pytest.fixture
def client():
    return TestClient(api.app)


def test_standalone_mode_without_a_hub(client, monkeypatch):
    monkeypatch.setattr(team, "hub_url", lambda: None)
    assert client.get("/auth/status").json() == {"mode": "standalone", "hub_url": None}


def test_team_mode_requires_a_signed_session(client, hub_key, tmp_path):
    assert client.get("/projects").status_code == 401
    forged = sign_token(load_or_create_key(tmp_path / "other.pem"),
                        {"typ": "session", "sub": 1, "org": 1, "admin": True, "exp": int(time.time()) + 60})
    assert client.get("/projects", headers={"Authorization": f"Bearer {forged}"}).status_code == 401


def test_an_expired_license_blocks_the_projects(client, hub_key):
    res = client.get("/projects", headers=_headers(hub_key, admin=True, license_exp=int(time.time()) - 10))
    assert res.status_code == 402 and "licencia" in res.json()["detail"]
    assert client.get("/projects", headers=_headers(hub_key, admin=True, org=2)).status_code == 402  # another org's license


def test_project_list_is_filtered_by_role(client, hub_key, monkeypatch):
    monkeypatch.setattr(api, "list_projects", lambda: [ProjectEntry("api", "API", "."), ProjectEntry("web", "Web", ".")])
    dev = client.get("/projects", headers=_headers(hub_key, roles={"api": "developer"})).json()
    assert [p["id"] for p in dev] == ["api"]
    admin = client.get("/projects", headers=_headers(hub_key, admin=True)).json()
    assert [p["id"] for p in admin] == ["api", "web"]


def test_scrum_roles_guard_the_routes(client, hub_key, monkeypatch):
    monkeypatch.setattr(api.services, "unregister_project", lambda *a, **k: None)
    dev = _headers(hub_key, roles={"api": "developer"})
    sm = _headers(hub_key, roles={"api": "scrum_master"})
    # A developer cannot delete, sync, register projects or change the model server.
    assert client.delete("/projects/api", headers=dev).status_code == 403
    assert client.post("/projects/api/sync", headers=dev).status_code == 403
    assert client.post("/projects", headers=dev, json={"id": "x", "name": "x", "repo_path": "."}).status_code == 403
    assert client.get("/config", headers=dev).status_code == 403
    # Nor read projects where they have no role.
    assert client.get("/projects/web/docs/tree", headers=dev).status_code == 403
    # The Scrum Master syncs; deleting is for the administrator.
    assert client.delete("/projects/api", headers=sm).status_code == 403
    assert client.delete("/projects/api", headers=_headers(hub_key, admin=True)).status_code == 200


def test_actions_are_reported_to_the_audit_trail(client, hub_key, monkeypatch):
    monkeypatch.setattr(api.services, "unregister_project", lambda *a, **k: None)
    client.delete("/projects/api", headers=_headers(hub_key, admin=True))
    client.delete("/projects/api", headers=_headers(hub_key, roles={"api": "developer"}))  # denied: not reported
    assert hub_key.reported == [("proyecto quitado de Beacon Local", "api")]


def test_start_issue_asks_the_code_with_the_issue_text(client, hub_key, monkeypatch):
    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"number": 7, "title": "Login falla con tildes", "body": "Al ingresar «José» se cae.", "url": "u", "labels": ["bug"]}

    calls = {}
    monkeypatch.setattr(api.httpx, "get", lambda url, headers, timeout: calls.setdefault("url", url) and FakeResponse())

    def fake_query(project_id, req):
        calls["question"] = req.question
        return {"answer": "Revisa auth.py", "sources": []}

    monkeypatch.setattr(api, "query", fake_query)
    res = client.post("/projects/api/issues/7/start", json={"language": "es"}, headers=_headers(hub_key, roles={"api": "developer"}))
    assert res.status_code == 200, res.text
    assert res.json()["issue"]["title"] == "Login falla con tildes" and res.json()["answer"] == "Revisa auth.py"
    assert calls["url"] == f"{HUB}/projects/api/issues/7"
    assert "Login falla con tildes" in calls["question"] and "José" in calls["question"]
    assert hub_key.reported == [("¿por dónde empiezo? (issue #7)", "api")]


def test_route_permission_table():
    assert team.required_permission("POST", "/projects/api/query") == ("ask", "api")
    assert team.required_permission("POST", "/projects/api/sync") == ("sync", "api")
    assert team.required_permission("DELETE", "/projects/api") == (team.ADMIN, None)
    assert team.required_permission("POST", "/projects/api/clean") == (team.ADMIN, None)
    assert team.required_permission("GET", "/system/ai-status") == (None, None)
    assert team.audit_action("POST", "/projects/api/query") == ("consulta al código", "api")
    assert team.audit_action("GET", "/projects/api/docs") is None
