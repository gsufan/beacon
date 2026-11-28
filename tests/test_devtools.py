"""Developer tools: frames of a pasted error located in the code, the history behind a piece of code and the daily
stand-up draft, plus their endpoints with a fake engine (no model server)."""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import git  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import core.api as api  # noqa: E402
from core import assist, devtools  # noqa: E402
from core.engine.retrieval import RetrievedChunk  # noqa: E402

PY_TRACE = """Traceback (most recent call last):
  File "/home/ana/app/.venv/lib/python3.11/site-packages/requests/api.py", line 59, in get
    return request("get", url)
  File "/home/ana/app/src/shop/cart.py", line 12, in total
    return sum(i.price for i in items) / count
ZeroDivisionError: division by zero"""


class FakeCollection:
    def __init__(self, chunks):
        self.chunks = chunks  # list of (metadata, code)

    def get(self, where=None, include=None):
        rows = [(m, c) for m, c in self.chunks if not where or all(m.get(k) == v for k, v in where.items())]
        return {"ids": [str(i) for i in range(len(rows))], "metadatas": [m for m, _ in rows], "documents": [c for _, c in rows]}


CART = ({"file_path": "src/shop/cart.py", "chunk_type": "function", "name": "total", "start_line": 10, "end_line": 13},
        "def total(items):\n    count = len(items)\n    return sum(i.price for i in items) / count")
CART_OVERVIEW = ({"file_path": "src/shop/cart.py", "chunk_type": "overview", "name": "", "start_line": 1, "end_line": 40}, "módulo")


def test_parse_frames_of_several_languages():
    assert [(f["path"], f["line"], f["func"]) for f in devtools.parse_frames(PY_TRACE)] == [
        ("/home/ana/app/.venv/lib/python3.11/site-packages/requests/api.py", 59, "get"), ("/home/ana/app/src/shop/cart.py", 12, "total")]
    js = "TypeError: Cannot read properties of undefined\n    at handle (/srv/app/src/routes/cart.ts:12:9)\n    at /srv/app/src/index.ts:40:3"
    assert [(f["path"], f["line"]) for f in devtools.parse_frames(js)] == [("/srv/app/src/routes/cart.ts", 12), ("/srv/app/src/index.ts", 40)]
    java = "java.lang.NullPointerException\n\tat com.acme.shop.Order.total(Order.java:57)"
    assert devtools.parse_frames(java)[0] == {"path": "Order.java", "line": 57, "func": "com.acme.shop.Order.total"}
    go = "panic: runtime error\n\t/home/ana/app/internal/cart/cart.go:33 +0x1d"
    assert devtools.parse_frames(go)[0]["path"] == "/home/ana/app/internal/cart/cart.go"


def test_error_message():
    assert devtools.error_message(PY_TRACE) == "ZeroDivisionError: division by zero"
    assert devtools.error_message("at x (a.js:1:1)\nTypeError: boom") == "TypeError: boom"


def test_match_repo_file_by_longest_suffix_and_java_package():
    files = ["src/shop/cart.py", "tests/cart.py", "src/main/java/com/acme/shop/Order.java", "src/main/java/com/acme/old/Order.java"]
    assert devtools.match_repo_file("/home/ana/app/src/shop/cart.py", files) == "src/shop/cart.py"
    assert devtools.match_repo_file("C:/x/requests/api.py", files) is None
    assert devtools.match_repo_file("Order.java", files, "com.acme.shop.Order.total") == "src/main/java/com/acme/shop/Order.java"


def test_locate_finds_the_function_that_contains_the_line():
    frames = devtools.locate(FakeCollection([CART_OVERVIEW, CART]), devtools.parse_frames(PY_TRACE), ["src/shop/cart.py"])
    assert frames[0]["file_path"] is None and frames[0]["chunk"] is None  # library
    assert frames[1]["chunk"]["name"] == "total" and frames[1]["file_path"] == "src/shop/cart.py"


@pytest.fixture
def repo(tmp_path):
    r = git.Repo.init(tmp_path)
    (tmp_path / "src").mkdir()
    f = tmp_path / "src" / "cart.py"
    steps = [("Ana", "def total(items):\n    return sum(items)\n", "feat: carro de compras (#3)"),
             ("Luis", "def total(items):\n    if not items:\n        return 0\n    return sum(items)\n", "fix: total de un carro vacío (#7)\n\nEl carro vacío devolvía None.")]
    for author, content, msg in steps:
        f.write_text(content)
        r.index.add(["src/cart.py"])
        r.index.commit(msg, author=git.Actor(author, f"{author.lower()}@acme.cl"))
    return tmp_path


def test_code_history_from_blame(repo):
    h = devtools.code_history(str(repo), "src/cart.py", 1, 4)
    assert h["lines"] == 4 and [c["author"] for c in h["commits"]] == ["Ana", "Luis"]
    luis = h["commits"][1]
    assert luis["issues"] == ["#7"] and "vacío devolvía None" in luis["body"] and luis["lines"] == 2
    assert h["created"]["issues"] == ["#3"] and h["shallow"] is False


def test_last_working_day_and_commits_since(repo):
    monday = datetime(2026, 11, 30, 9, 0)
    assert devtools.last_working_day(monday).date().isoformat() == "2026-11-27"
    assert devtools.last_working_day(datetime(2026, 12, 2, 9, 0)).date().isoformat() == "2026-12-01"
    commits = devtools.commits_since(str(repo), "Luis", datetime(2000, 1, 1))
    assert len(commits) == 1 and commits[0]["issues"] == ["#7"] and commits[0]["files"] == ["src/cart.py"]


class FakeEngine:
    def __init__(self, collection, chunks=()):
        self.collection = collection
        self.retriever = type("R", (), {"chunks_named": lambda _self, name: [c for c in chunks if c.name == name]})()


class FakeProject:
    def __init__(self, repo_path):
        self.repo_path = str(repo_path)
        self.docs_dir = str(Path(repo_path) / "docs")


def _client(monkeypatch, repo_path, engine, prompts):
    monkeypatch.setattr(api, "_get_engine", lambda pid: engine)
    monkeypatch.setattr(api, "get_project", lambda pid: FakeProject(repo_path))
    monkeypatch.setattr(assist, "_ask_model", lambda eng, system, user, max_tokens=900: prompts.append(user) or "explicación")
    return TestClient(api.app)


def test_explain_error_endpoint(monkeypatch, repo):
    (repo / "src" / "shop").mkdir()
    (repo / "src" / "shop" / "cart.py").write_text("x = 1\n")
    git.Repo(repo).index.add(["src/shop/cart.py"])
    git.Repo(repo).index.commit("add shop", author=git.Actor("Ana", "ana@acme.cl"))
    prompts = []
    client = _client(monkeypatch, repo, FakeEngine(FakeCollection([CART])), prompts)
    r = client.post("/projects/p/explain-error", json={"trace": PY_TRACE}).json()
    assert r["in_project"] and r["explanation"] == "explicación" and r["message"].startswith("ZeroDivisionError")
    assert [s["name"] for s in r["sources"]] == ["total"] and r["frames"][1]["name"] == "total"
    assert "falla en la línea 12" in prompts[0] and "site-packages/requests/api.py:59" in prompts[0]


def test_why_endpoint_by_function_name(monkeypatch, repo):
    chunk = RetrievedChunk("src/cart.py", "function", "total", 1, 4, "def total(items): ...", -1.0)
    prompts = []
    client = _client(monkeypatch, repo, FakeEngine(FakeCollection([]), [chunk]), prompts)
    r = client.post("/projects/p/why", json={"name": "total"}).json()
    assert r["explanation"] == "explicación" and [c["author"] for c in r["commits"]] == ["Ana", "Luis"]
    assert "fix: total de un carro vacío (#7)" in prompts[0]
    assert client.post("/projects/p/why", json={"name": "nada"}).status_code == 404
    assert client.post("/projects/p/why", json={}).status_code == 400


def test_standup_endpoint(monkeypatch, repo):
    monkeypatch.setattr(devtools, "last_working_day", lambda now=None: datetime(2000, 1, 1).astimezone())
    prompts = []
    client = _client(monkeypatch, repo, FakeEngine(FakeCollection([])), prompts)
    r = client.post("/projects/p/standup", json={"person": "Luis", "issues": [{"ref": "#9", "title": "Cupones", "labels": ["bloqueado"]}]}).json()
    assert r["draft"] == "explicación" and len(r["local_commits"]) == 1
    assert "fix: total de un carro vacío (#7)" in prompts[0] and "#9 Cupones" in prompts[0] and "bloqueado" in prompts[0]
