"""Review before the pull request: local changes against the base branch, the indexed functions they touch, missing
tests, verified knowledge they would leave pending and suggested reviewers, plus its endpoint (no model server)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import git  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import core.api as api  # noqa: E402
from core import assist, review  # noqa: E402

CART_V1 = "def total(items):\n    return sum(items)\n\n\ndef count(items):\n    return len(items)\n"
CART_V2 = ("def total(items):\n    if not items:\n        return 0\n    return sum(items)\n\n\ndef count(items):\n    return len(items)\n\n\n"
           "def discount(items, pct):\n    return total(items) * (1 - pct)\n")


class FakeCollection:
    """The part of the Chroma API that review and impact use."""

    def __init__(self, chunks):
        self.chunks = chunks  # list of (metadata, code)

    def get(self, where=None, where_document=None, include=None):
        rows = [(str(i), m, c) for i, (m, c) in enumerate(self.chunks)
                if (not where or all(m.get(k) == v for k, v in where.items()))
                and (not where_document or where_document["$contains"] in c)]
        return {"ids": [r[0] for r in rows], "metadatas": [r[1] for r in rows], "documents": [r[2] for r in rows]}


INDEX = FakeCollection([
    ({"file_path": "src/cart.py", "chunk_type": "function", "name": "total", "start_line": 1, "end_line": 2}, "def total(items):\n    return sum(items)"),
    ({"file_path": "src/cart.py", "chunk_type": "function", "name": "count", "start_line": 5, "end_line": 6}, "def count(items):\n    return len(items)"),
    ({"file_path": "src/checkout.py", "chunk_type": "function", "name": "pay", "start_line": 1, "end_line": 3}, "def pay(items):\n    return total(items)"),
    ({"file_path": "tests/test_checkout.py", "chunk_type": "function", "name": "test_pay", "start_line": 1, "end_line": 2},
     "def test_pay():\n    assert pay([1]) == 1"),
])


def _commit(repo, root, files, msg, author):
    for path, text in files.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(text)
        repo.index.add([path])
    repo.index.commit(msg, author=git.Actor(author, f"{author.lower()}@acme.cl"), committer=git.Actor(author, f"{author.lower()}@acme.cl"))


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    r = git.Repo.init(root, initial_branch="main")
    with r.config_writer() as cw:
        cw.set_value("user", "name", "Diego")
        cw.set_value("user", "email", "diego@acme.cl")
    _commit(r, root, {"src/cart.py": CART_V1, "src/checkout.py": "def pay(items):\n    return total(items)\n"}, "feat: carro", "Ana")
    _commit(r, root, {"src/checkout.py": "def pay(items):\n    # paga\n    return total(items)\n"}, "docs: pago", "Luis")
    r.git.checkout("-b", "feature/42-carro-vacio")
    _commit(r, root, {"src/cart.py": CART_V2}, "fix: total de un carro vacío (#42)", "Diego")
    (root / "src" / "coupons.py").write_text("def apply_coupon(code):\n    return code.upper()\n")  # untracked
    return root


def test_parse_diff_statuses_and_ranges():
    raw = ("diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -3,0 +4,2 @@ def f():\n+def g():\n+    pass\n"
           "diff --git a/b.py b/b.py\n--- a/b.py\n+++ /dev/null\n@@ -1,2 +0,0 @@\n-x = 1\n-y = 2\n")
    a, b = review.parse_diff(raw)
    assert (a["status"], a["ranges"], a["names"], a["added"]) == ("M", [(4, 5)], ["g"], 2)
    assert (b["path"], b["status"], b["removed"]) == ("b.py", "D", 2)


def test_build_review(repo, tmp_path):
    project_dir = tmp_path / "data"
    project_dir.mkdir()
    (project_dir / "verified_answers.json").write_text(json.dumps([
        {"id": "a1", "question": "¿Cómo se calcula el total?", "reviewer": "Ana", "files": {"src/cart.py": "x"}},
        {"id": "a2", "question": "¿Cómo se paga?", "reviewer": "Ana", "files": {"src/checkout.py": "y"}}]))
    r = review.build(INDEX, str(repo), project_dir, project_dir / "docs", None)
    assert r["base"] == "main" and r["branch"] == "feature/42-carro-vacio" and r["issues"] == ["#42"]
    assert {f["path"]: f["status"] for f in r["files"]} == {"src/cart.py": "M", "src/coupons.py": "A"}
    fns = {fn["name"]: fn for fn in r["functions"]}
    assert set(fns) == {"total", "discount", "apply_coupon"}  # count did not change
    assert fns["total"]["callers"] == 1 and fns["total"]["tests"] == ["tests/test_checkout.py"] and not fns["total"]["missing_test"]
    assert fns["discount"]["new"] and fns["discount"]["missing_test"]
    assert r["tests_to_run"] == ["tests/test_checkout.py"]
    assert [a["id"] for a in r["verified_answers"]] == ["a1"]
    assert [x["name"] for x in r["reviewers"]] == ["Ana"]  # Diego is the author
    assert "return 0" in r["_diff"]


def test_test_in_the_change_counts(repo, tmp_path):
    (repo / "tests").mkdir()
    (repo / "tests" / "test_cart.py").write_text("from cart import discount\n\ndef test_discount():\n    assert discount([10], 0.5) == 5\n")
    r = review.build(INDEX, str(repo), tmp_path, tmp_path / "docs", None)
    assert next(fn for fn in r["functions"] if fn["name"] == "discount")["tested_in_change"]
    assert "tests/test_cart.py" in r["tests_to_run"]


def test_invalid_base(repo, tmp_path):
    with pytest.raises(review.InvalidBaseError):
        review.build(INDEX, str(repo), tmp_path, tmp_path, None, "--output=x")
    with pytest.raises(review.InvalidBaseError):
        review.build(INDEX, str(repo), tmp_path, tmp_path, None, "no-existe")


def test_review_endpoint(monkeypatch, repo, tmp_path):
    engine = type("E", (), {"collection": INDEX})()
    project = type("P", (), {"repo_path": str(repo), "docs_dir": str(tmp_path / "docs")})()
    prompts = []
    monkeypatch.setattr(api, "_get_engine", lambda pid: engine)
    monkeypatch.setattr(api, "get_project", lambda pid: project)
    monkeypatch.setattr(api, "_hub_public_key", lambda: None)
    monkeypatch.setattr(assist, "_ask_model", lambda eng, system, user, max_tokens=900: prompts.append(user) or "## Resumen")
    client = TestClient(api.app)
    r = client.post("/projects/p/review", json={}).json()
    assert r["description"] == "## Resumen" and "_diff" not in r and "Issues: #42" in prompts[0] and "discount" in prompts[0]
    assert client.post("/projects/p/review", json={"describe": False}).json()["description"] == ""
    assert client.post("/projects/p/review", json={"base": "-x"}).status_code == 400
