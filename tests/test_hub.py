"""Beacon Hub: accounts, Scrum roles, licenses with a trial, audit and the GitHub connection."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from hub import roles as R  # noqa: E402
from hub.app import Hub, create_app  # noqa: E402
from hub.github import GitHubClient, issue_refs, normalize_repo  # noqa: E402
from hub.security import InvalidTokenError, hash_password, verify_password, verify_token  # noqa: E402


def _fake_github(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/repos/acme/api/issues":
        issues = [
            {"number": 7, "title": "Login falla con tildes", "body": "Al ingresar «José»...", "state": "open",
             "html_url": "https://github.com/acme/api/issues/7", "user": {"login": "ana"},
             "assignees": [{"login": "ana"}], "labels": [{"name": "bug"}], "updated_at": "2026-11-25T10:00:00Z", "comments": 1},
            {"number": 8, "title": "Un PR", "pull_request": {}, "state": "open", "html_url": "x", "user": {"login": "b"}},
        ]
        if request.url.params.get("assignee") == "luis":
            issues = []
        return httpx.Response(200, json=issues)
    if path == "/repos/acme/api/issues/7":
        return httpx.Response(200, json={"number": 7, "title": "Login falla con tildes", "body": "Al ingresar «José»...",
                                         "state": "open", "html_url": "u", "user": {"login": "ana"}, "assignees": [], "labels": []})
    if path == "/repos/acme/api/commits":
        return httpx.Response(200, json=[{"sha": "abcdef1234", "html_url": "c",
                                          "commit": {"message": "fix: normaliza tildes (#7)\n\ndetalle",
                                                     "author": {"name": "Ana", "date": "2026-11-25T09:00:00Z"}},
                                          "author": {"login": "ana"}}])
    return httpx.Response(404, json={})


@pytest.fixture
def client(tmp_path):
    hub = Hub(tmp_path, github_factory=lambda token: GitHubClient(token, transport=httpx.MockTransport(_fake_github)))
    c = TestClient(create_app(hub))
    c.hub = hub
    return c


def _auth(res) -> dict:
    return {"Authorization": f"Bearer {res.json()['session']}"}


def _signup(client, email="admin@acme.cl"):
    res = client.post("/signup", json={"org_name": "Acme", "name": "Admin", "email": email, "password": "secreta123"})
    assert res.status_code == 201, res.text
    return res


def _invite(client, admin_headers, email, name):
    code = client.post("/invitations", json={"email": email}, headers=admin_headers).json()["code"]
    res = client.post("/invitations/accept", json={"code": code, "name": name, "password": "secreta123"})
    assert res.status_code == 201, res.text
    return res


def test_passwords_are_hashed_and_verified():
    stored = hash_password("una clave")
    assert "una clave" not in stored
    assert verify_password("una clave", stored) and not verify_password("otra", stored)


def test_signup_creates_an_admin_with_a_30_day_trial_license(client):
    res = _signup(client)
    body = res.json()
    assert body["user"]["admin"] is True
    lic = verify_token(body["public_key"], body["license"], "license")
    assert lic["plan"] == "trial" and lic["seats"] == 5
    assert 29 * 86400 < lic["exp"] - time.time() <= 30 * 86400
    with pytest.raises(InvalidTokenError):  # a license is not a session
        verify_token(body["public_key"], body["license"], "session")


def test_login_rejects_a_wrong_password_and_duplicate_emails(client):
    _signup(client)
    assert client.post("/auth/login", json={"email": "admin@acme.cl", "password": "mala"}).status_code == 401
    assert client.post("/auth/login", json={"email": "ADMIN@acme.cl", "password": "secreta123"}).status_code == 200
    assert client.post("/signup", json={"org_name": "Otra", "name": "Xi", "email": "admin@acme.cl", "password": "secreta123"}).status_code == 409


def test_a_tampered_session_is_rejected(client):
    token = _signup(client).json()["session"]
    body, sig = token.split(".")
    assert client.get("/me", headers={"Authorization": f"Bearer {body}x.{sig}"}).status_code == 401


def test_invitations_respect_the_seats_of_the_plan(client):
    admin = _auth(_signup(client))
    for i in range(4):
        _invite(client, admin, f"dev{i}@acme.cl", f"Dev {i}")
    code = client.post("/invitations", json={"email": "extra@acme.cl"}, headers=admin).json()["code"]
    res = client.post("/invitations/accept", json={"code": code, "name": "Extra", "password": "secreta123"})
    assert res.status_code == 402
    assert client.post("/invitations/accept", json={"code": "inventado", "name": "Xi", "password": "secreta123"}).status_code == 404


def test_scrum_roles_decide_what_each_person_can_do(client):
    admin = _auth(_signup(client))
    po = _invite(client, admin, "po@acme.cl", "Paula PO")
    dev = _invite(client, admin, "dev@acme.cl", "Diego Dev")
    po_id, dev_id = po.json()["user"]["sub"], dev.json()["user"]["sub"]
    assert client.post("/projects", json={"id": "api", "name": "API", "github_repo": "https://github.com/acme/api"}, headers=admin).json()["github_repo"] == "acme/api"
    assert client.put(f"/projects/api/members/{po_id}", json={"role": "product_owner"}, headers=admin).status_code == 200
    po_h = _auth(client.post("/auth/login", json={"email": "po@acme.cl", "password": "secreta123"}))
    # The Product Owner manages the members of their project; a developer does not.
    assert client.put(f"/projects/api/members/{dev_id}", json={"role": "developer"}, headers=po_h).status_code == 200
    dev_h = _auth(client.post("/auth/login", json={"email": "dev@acme.cl", "password": "secreta123"}))
    assert client.put(f"/projects/api/members/{po_id}", json={"role": "developer"}, headers=dev_h).status_code == 403
    # Only the administrator creates or deletes projects.
    assert client.post("/projects", json={"id": "otro", "name": "Otro"}, headers=po_h).status_code == 403
    assert client.delete("/projects/api", headers=dev_h).status_code == 403
    # The developer sees the project with their role, not the emails of the team.
    projects = client.get("/projects", headers=dev_h).json()["projects"]
    assert [(p["id"], p["role"]) for p in projects] == [("api", "developer")]
    assert all("email" not in u for u in client.get("/users", headers=dev_h).json()["users"])
    # A role change reaches the session with /auth/refresh, without logging in again.
    client.put(f"/projects/api/members/{dev_id}", json={"role": "scrum_master"}, headers=admin)
    assert client.post("/auth/refresh", headers=dev_h).json()["user"]["roles"] == {"api": "scrum_master"}


def test_permission_matrix():
    dev = {"roles": {"api": R.DEVELOPER}}
    sm = {"roles": {"api": R.SCRUM_MASTER}}
    assert R.can(dev, R.ASK, "api") and not R.can(dev, R.SYNC, "api")
    assert R.can(sm, R.SYNC, "api") and not R.can(sm, R.MANAGE_MEMBERS, "api")
    assert not R.can(dev, R.ASK, "otro")
    assert R.can({"admin": True}, R.MANAGE_MEMBERS, "cualquiera")
    assert not R.can(dev, R.ASK)  # actions outside a project are for the administrator


def test_audit_records_who_did_what_and_hides_it_from_developers(client):
    admin = _auth(_signup(client))
    dev = _invite(client, admin, "dev@acme.cl", "Diego Dev")
    client.post("/projects", json={"id": "api", "name": "API"}, headers=admin)
    client.put(f"/projects/api/members/{dev.json()['user']['sub']}", json={"role": "developer"}, headers=admin)
    dev_h = _auth(client.post("/auth/login", json={"email": "dev@acme.cl", "password": "secreta123"}))
    assert client.post("/audit", json={"project_id": "api", "action": "consulta"}, headers=dev_h).status_code == 201
    events = client.get("/audit?project_id=api", headers=admin).json()["events"]
    assert events[0]["action"] == "consulta" and events[0]["user_name"] == "Diego Dev" and events[0]["source"] == "local"
    assert client.get("/audit?project_id=api", headers=dev_h).status_code == 403


def test_choosing_a_plan_extends_the_license(client):
    admin = _auth(_signup(client))
    assert client.post("/org/plan", json={"plan": "inventado"}, headers=admin).status_code == 400
    res = client.post("/org/plan", json={"plan": "equipo"}, headers=admin)
    lic = verify_token(res.json()["public_key"], res.json()["license"], "license")
    assert lic["plan"] == "equipo" and lic["seats"] == 15
    org = client.get("/org", headers=admin).json()
    assert org["plan_label"] == "Equipo" and not org["expired"]


def test_github_issues_and_activity_are_metadata_of_the_linked_repository(client):
    admin = _auth(_signup(client))
    client.post("/projects", json={"id": "api", "name": "API"}, headers=admin)
    assert client.get("/projects/api/github/issues", headers=admin).status_code == 409  # no repository linked yet
    client.patch("/projects/api", json={"github_repo": "acme/api"}, headers=admin)
    issues = client.get("/projects/api/github/issues", headers=admin).json()["issues"]
    assert [i["number"] for i in issues] == [7]  # the pull request is left out
    assert client.get("/projects/api/github/issues?mine=true", headers=admin).json()["needs_github_login"] is True
    me = client.get("/me", headers=admin).json()["user"]["sub"]
    client.patch(f"/users/{me}", json={"github_login": "luis"}, headers=admin)
    assert client.get("/projects/api/github/issues?mine=true", headers=admin).json()["issues"] == []
    commit = client.get("/projects/api/github/activity", headers=admin).json()["commits"][0]
    assert commit["author"] == "ana" and commit["issues"] == [7] and commit["message"] == "fix: normaliza tildes (#7)"
    assert client.get("/projects/api/github/issues/7", headers=admin).json()["title"] == "Login falla con tildes"


def test_repository_names_and_issue_references():
    assert normalize_repo("https://github.com/acme/api.git") == "acme/api"
    with pytest.raises(ValueError):
        normalize_repo("no es un repo")
    assert issue_refs("fix #12 y #3, no a/b#9") == [3, 12]
