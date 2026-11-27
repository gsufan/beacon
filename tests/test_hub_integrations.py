"""Team tools in Beacon Hub (Jira, writing to the backlog, Slack) and who knows a file, from the local git history."""
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import git  # noqa: E402
import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from core import expertise  # noqa: E402
from hub.app import Hub, create_app  # noqa: E402
from hub.db import Database  # noqa: E402
from hub.github import GitHubClient  # noqa: E402
from hub.jira import JiraClient, adf_doc, adf_text, normalize_project, normalize_site  # noqa: E402
from hub.slack import SlackClient, normalize_webhook  # noqa: E402

JIRA_SITE = "https://acme.atlassian.net"
WEBHOOK = "https://hooks.slack.com/services/T000/B000/XXXX"


class Recorder:
    def __init__(self):
        self.requests = []

    def jira(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == "/rest/api/3/search/jql":
            issue = {"key": "BEAC-12", "fields": {
                "summary": "Exportar falla con nombres largos",
                "description": {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Al exportar "}, {"type": "text", "text": "se corta."}]}]},
                "assignee": {"displayName": "Diego Muñoz"}, "labels": ["bug"], "updated": "2026-11-27T10:00:00.000+0000",
                "status": {"name": "En curso"}, "comment": {"total": 2}}}
            return httpx.Response(200, json={"issues": [issue]})
        if path == "/rest/api/3/issue/BEAC-12":
            return httpx.Response(200, json={"key": "BEAC-12", "fields": {"summary": "Exportar falla con nombres largos", "description": None}})
        if path == "/rest/api/3/issue/BEAC-12/comment":
            return httpx.Response(201, json={"id": "10001"})
        return httpx.Response(404, json={})

    def github(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/repos/acme/api/issues/7/comments":
            return httpx.Response(201, json={"html_url": "https://github.com/acme/api/issues/7#issuecomment-1"})
        if request.url.path == "/repos/acme/api/issues":
            return httpx.Response(200, json=[])
        return httpx.Response(404, json={})

    def slack(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(200, text="ok")


@pytest.fixture
def env(tmp_path):
    rec = Recorder()
    slack_sent = []

    class SyncSlack(SlackClient):
        def send_later(self, text):  # synchronous in tests, so the notices can be checked
            slack_sent.append(text)

    hub = Hub(tmp_path,
              github_factory=lambda token: GitHubClient(token, transport=httpx.MockTransport(rec.github)),
              jira_factory=lambda site, email, token: JiraClient(site, email, token, transport=httpx.MockTransport(rec.jira)),
              slack_factory=lambda webhook: SyncSlack(webhook, transport=httpx.MockTransport(rec.slack)))
    client = TestClient(create_app(hub))
    res = client.post("/signup", json={"org_name": "Acme", "name": "Ana Admin", "email": "ana@acme.cl", "password": "secreta123"})
    h = {"Authorization": f"Bearer {res.json()['session']}"}
    return client, h, rec, slack_sent


def _invite_dev(client, h):
    code = client.post("/invitations", json={"email": "diego@acme.cl"}, headers=h).json()["code"]
    res = client.post("/invitations/accept", json={"code": code, "name": "Diego Muñoz", "password": "secreta123"})
    return res.json()["user"]["sub"], {"Authorization": f"Bearer {res.json()['session']}"}


def test_jira_backlog_issues_mine_and_comment(env):
    client, h, rec, _ = env
    assert client.put("/org/jira", json={"site": "http://evil.example", "email": "a@b.cl", "token": "t"}, headers=h).status_code == 400
    assert client.put("/org/jira", json={"site": JIRA_SITE, "email": "bot@acme.cl", "token": "tok"}, headers=h).json() == {"jira_connected": True}
    client.post("/projects", json={"id": "api", "name": "API", "jira_project": "beac"}, headers=h)
    dev_id, dev_h = _invite_dev(client, h)
    client.put(f"/projects/api/members/{dev_id}", json={"role": "developer"}, headers=h)
    # Without a Jira user, "mine" asks for it; with it, the JQL filters by that person.
    assert client.get("/projects/api/issues?mine=true", headers=dev_h).json()["needs_login"] is True
    client.patch(f"/users/{dev_id}", json={"jira_user": "diego@acme.cl"}, headers=dev_h)
    data = client.get("/projects/api/issues?mine=true", headers=dev_h).json()
    assert data["source"] == "jira" and data["issues"][0]["ref"] == "BEAC-12"
    assert data["issues"][0]["body"] == "Al exportar se corta."
    jql = rec.requests[-1].url.params["jql"]
    assert 'project = "BEAC"' in jql and 'assignee = "diego@acme.cl"' in jql
    # A developer may comment on the issue; the comment is signed and recorded in the audit trail.
    res = client.post("/projects/api/issues/BEAC-12/comment", json={"body": "Revisar portability.py"}, headers=dev_h)
    assert res.status_code == 200 and "BEAC-12" in res.json()["url"]
    sent = json.loads(rec.requests[-1].content)
    assert "Revisar portability.py" in adf_text(sent["body"]) and "Diego Muñoz" in adf_text(sent["body"])
    events = client.get("/audit?project_id=api", headers=h).json()["events"]
    assert events[0]["action"] == "comentario en la issue" and events[0]["detail"] == "BEAC-12"


def test_github_backlog_comment_and_other_members_work(env):
    client, h, rec, _ = env
    client.post("/projects", json={"id": "api", "name": "API", "github_repo": "acme/api"}, headers=h)
    dev_id, _ = _invite_dev(client, h)
    res = client.post("/projects/api/issues/7/comment", json={"body": "Empezar por auth.py"}, headers=h)
    assert res.json()["url"].endswith("#issuecomment-1")
    assert "Empezar por auth.py" in json.loads(rec.requests[-1].content)["body"]
    assert client.post("/projects/api/issues/abc/comment", json={"body": "x"}, headers=h).status_code == 400
    # Another member's work (the vacation case): needs that person's GitHub user.
    data = client.get(f"/projects/api/issues?assignee_id={dev_id}", headers=h).json()
    assert data["needs_login"] is True and data["person"] == "Diego Muñoz"
    client.patch(f"/users/{dev_id}", json={"github_login": "diego"}, headers=h)
    client.get(f"/projects/api/issues?assignee_id={dev_id}", headers=h)
    assert rec.requests[-1].url.params["assignee"] == "diego"


def test_backlog_needs_a_linked_tracker(env):
    client, h, _, _ = env
    client.post("/projects", json={"id": "api", "name": "API"}, headers=h)
    assert client.get("/projects/api/issues", headers=h).status_code == 409


def test_slack_share_and_team_notices(env):
    client, h, rec, notices = env
    assert client.put("/org/slack", json={"webhook": "https://example.com/hook"}, headers=h).status_code == 400
    assert client.post("/org/slack/test", headers=h).status_code == 409
    client.put("/org/slack", json={"webhook": WEBHOOK}, headers=h)
    assert client.post("/org/slack/test", headers=h).status_code == 200
    assert str(rec.requests[-1].url) == WEBHOOK
    client.post("/projects", json={"id": "api", "name": "API", "github_repo": "acme/api"}, headers=h)
    dev_id, dev_h = _invite_dev(client, h)
    client.put(f"/projects/api/members/{dev_id}", json={"role": "developer"}, headers=h)
    assert any("se unió al equipo" in n for n in notices) and any("Desarrollador en api" in n for n in notices)
    res = client.post("/projects/api/slack", json={"text": "Por dónde empezar #7: auth.py"}, headers=dev_h)
    assert res.status_code == 200
    assert "Diego Muñoz" in json.loads(rec.requests[-1].content)["text"]
    # Sync reported by Beacon Local is announced; plain queries are not.
    client.post("/audit", json={"project_id": "api", "action": "sincronización"}, headers=dev_h)
    client.post("/audit", json={"project_id": "api", "action": "consulta al código"}, headers=dev_h)
    assert any("sincronización en api" in n for n in notices) and not any("consulta" in n for n in notices)
    assert client.get("/org", headers=h).json()["slack_connected"] is True


def test_integration_values_are_validated():
    assert normalize_site("https://acme.atlassian.net/") == JIRA_SITE
    assert normalize_project(" beac ") == "BEAC"
    with pytest.raises(ValueError):
        normalize_project("no válido")
    with pytest.raises(ValueError):
        normalize_webhook("https://hooks.slack.com.evil.cl/services/x")
    assert adf_text(adf_doc("uno\ndos")).split() == ["uno", "dos"]


def test_existing_databases_get_the_new_columns(tmp_path):
    con = sqlite3.connect(tmp_path / "hub.sqlite3")
    con.execute("CREATE TABLE orgs (id INTEGER PRIMARY KEY, name TEXT NOT NULL, plan TEXT NOT NULL, seats INTEGER NOT NULL, "
                "period_end REAL NOT NULL, github_token TEXT, created REAL NOT NULL)")
    con.commit()
    con.close()
    db = Database(tmp_path / "hub.sqlite3")
    cols = {r["name"] for r in db.query("PRAGMA table_info(orgs)")}
    assert {"jira_site", "jira_token", "slack_webhook"} <= cols


def test_experts_come_from_the_git_history(tmp_path):
    repo = git.Repo.init(tmp_path)
    for name, content in [("Ana", "a = 1\n"), ("Luis", "a = 2\n"), ("Ana", "a = 3\n")]:
        (tmp_path / "app.py").write_text(content)
        repo.index.add(["app.py"])
        repo.index.commit("cambio", author=git.Actor(name, f"{name.lower()}@acme.cl"))
    data = expertise.experts(str(tmp_path), ["app.py"])
    assert data["files"][0]["authors"][0] == {"name": "Ana", "commits": 2}
    assert data["overall"][0]["name"] == "Ana" and "acme.cl" not in json.dumps(data)
    with pytest.raises(expertise.InvalidPathError):
        expertise.experts(str(tmp_path), ["../secreto.txt"])
