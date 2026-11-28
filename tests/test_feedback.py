"""Thumbs up or down on answers: anonymous storage, the tech lead's queue, closing votes and who may see the queue."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fastapi.testclient import TestClient  # noqa: E402

import core.api as api  # noqa: E402
from core import feedback, team  # noqa: E402
from hub import roles as R  # noqa: E402

SRC = [{"file_path": "src/cart.py", "chunk_type": "function", "name": "total", "start_line": 1, "end_line": 4, "code": "def total(): ..."}]


def test_votes_queue_and_resolution(tmp_path):
    down = feedback.add(tmp_path, "down", "¿Cómo se calcula el total?", "No sé.", SRC, "No menciona los descuentos")
    again = feedback.add(tmp_path, "down", "¿Cómo se calcula el total?", "No sé.", SRC, "Falta el IVA")
    up = feedback.add(tmp_path, "up", "¿Quién llama a pay?", "checkout.", [])
    assert again["id"] == down["id"] and again["count"] == 2 and again["comments"] == ["No menciona los descuentos", "Falta el IVA"]
    assert "code" not in again["sources"][0] and "person" not in again  # neither code nor who voted
    q = feedback.queue(tmp_path)
    assert [i["id"] for i in q["to_fix"]] == [down["id"]] and [i["id"] for i in q["to_verify"]] == [up["id"]]
    assert q["totals"] == {"up": 1, "down": 2, "resolved": 0}
    assert feedback.resolve(tmp_path, up["id"], "dismissed") and not feedback.resolve(tmp_path, up["id"], "dismissed")
    assert feedback.resolve_question(tmp_path, "¿Cómo se calcula el total? ") == 1
    q = feedback.queue(tmp_path)
    assert q["to_fix"] == [] and q["to_verify"] == [] and q["totals"]["resolved"] == 2


def test_resolved_votes_are_trimmed_first(tmp_path, monkeypatch):
    monkeypatch.setattr(feedback, "MAX_ITEMS", 3)
    a = feedback.add(tmp_path, "up", "q1", "a", [])
    feedback.resolve(tmp_path, a["id"], "dismissed")
    for n in range(3):
        feedback.add(tmp_path, "down", f"q{n + 2}", "a", [])
    q = feedback.queue(tmp_path)
    assert len(q["to_fix"]) == 3 and q["totals"]["resolved"] == 0


def test_endpoints(monkeypatch, tmp_path):
    project = type("P", (), {"docs_dir": str(tmp_path / "docs"), "repo_path": str(tmp_path)})()
    monkeypatch.setattr(api, "get_project", lambda pid: project)
    client = TestClient(api.app)
    body = {"vote": "down", "question": "¿Qué hace total?", "answer": "Suma.", "sources": SRC, "comment": "Incompleta"}
    r = client.post("/projects/p/feedback", json=body).json()
    assert r["count"] == 1
    assert client.post("/projects/p/feedback", json={**body, "vote": "meh"}).status_code == 422
    q = client.get("/projects/p/feedback").json()
    assert q["to_fix"][0]["comments"] == ["Incompleta"]
    assert client.post(f"/projects/p/feedback/{r['id']}/resolve", json={"outcome": "verified"}).json() == {"status": "ok"}
    assert client.post(f"/projects/p/feedback/{r['id']}/resolve", json={"outcome": "verified"}).status_code == 404


def test_permissions():
    assert team.required_permission("POST", "/projects/p/feedback") == (R.ASK, "p")
    assert team.required_permission("GET", "/projects/p/feedback") == (R.VERIFY, "p")
    assert team.required_permission("POST", "/projects/p/feedback/abc/resolve") == (R.VERIFY, "p")
