"""Followed files: the commits that changed them since the person last looked, per person, and the endpoints."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import git  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import core.api as api  # noqa: E402
from core import follows  # noqa: E402


def _commit(repo, root, path, text, msg, author="Ana"):
    (root / path).parent.mkdir(parents=True, exist_ok=True)
    (root / path).write_text(text)
    repo.index.add([path])
    actor = git.Actor(author, f"{author.lower()}@acme.cl")
    repo.index.commit(msg, author=actor, committer=actor)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    r = git.Repo.init(root)
    _commit(r, root, "src/cart.py", "v1\n", "feat: carro")
    _commit(r, root, "src/pay.py", "v1\n", "feat: pago")
    return r, root


def test_changes_since_followed_and_seen(repo, tmp_path):
    r, root = repo
    data = tmp_path / "data"
    follows.follow(data, str(root), "7", "src/cart.py")
    follows.follow(data, str(root), "7", "src/new.py")  # does not exist yet
    assert follows.status(data, str(root), "7")["changed"] == 0
    _commit(r, root, "src/cart.py", "v2\n", "fix: total vacío (#3)", "Luis")
    _commit(r, root, "src/pay.py", "v2\n", "fix: pago")  # not followed
    _commit(r, root, "src/new.py", "x\n", "feat: nuevo")
    st = follows.status(data, str(root), "7")
    assert st["changed"] == 2 and st["files"][0]["file_path"] == "src/cart.py"
    assert [(c["author"], c["subject"]) for c in st["files"][0]["changes"]] == [("Luis", "fix: total vacío (#3)")]
    assert follows.status(data, str(root), "8")["files"] == []  # per person
    follows.mark_seen(data, str(root), "7", "src/cart.py")
    assert [f["file_path"] for f in follows.status(data, str(root), "7")["files"] if f["changes"]] == ["src/new.py"]
    follows.mark_seen(data, str(root), "7")
    assert follows.status(data, str(root), "7")["changed"] == 0
    assert follows.unfollow(data, "7", "src/cart.py") and not follows.unfollow(data, "7", "src/cart.py")


def test_rewritten_history_reports_nothing(repo, tmp_path):
    r, root = repo
    data = tmp_path / "data"
    _commit(r, root, "src/cart.py", "v2\n", "wip")
    follows.follow(data, str(root), "local", "src/cart.py")
    r.git.reset("--hard", "HEAD~1")  # the anchor is no longer in the history
    assert follows.status(data, str(root), "local")["files"][0]["changes"] == []


def test_endpoints(monkeypatch, repo, tmp_path):
    r, root = repo
    project = type("P", (), {"docs_dir": str(tmp_path / "data" / "docs"), "repo_path": str(root)})()
    monkeypatch.setattr(api, "get_project", lambda pid: project)
    client = TestClient(api.app)
    assert client.post("/projects/p/follows", json={"file_path": "src/cart.py"}).json() == {"status": "ok"}
    assert client.post("/projects/p/follows", json={"file_path": "../etc/passwd"}).status_code == 400
    _commit(r, root, "src/cart.py", "v2\n", "fix: carro")
    st = client.get("/projects/p/follows").json()
    assert st["changed"] == 1 and st["files"][0]["changes"][0]["subject"] == "fix: carro"
    client.post("/projects/p/follows/seen", json={})
    assert client.get("/projects/p/follows").json()["changed"] == 0
    assert client.delete("/projects/p/follows", params={"file_path": "src/cart.py"}).json() == {"status": "ok"}
    assert client.delete("/projects/p/follows", params={"file_path": "src/cart.py"}).status_code == 404
