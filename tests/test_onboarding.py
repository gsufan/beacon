"""Onboarding route: reading order by how much the rest of the code depends on each file, first issues by impact
radius, and its endpoint (no model server)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fastapi.testclient import TestClient  # noqa: E402

import core.api as api  # noqa: E402
from core import assist, onboarding  # noqa: E402
from core.engine.retrieval import RetrievedChunk  # noqa: E402

CHUNKS = [
    ({"file_path": "src/money.py", "chunk_type": "function", "name": "to_cents", "start_line": 1, "end_line": 3}, "def to_cents(x):\n    return round(x * 100)"),
    ({"file_path": "src/cart.py", "chunk_type": "function", "name": "total", "start_line": 1, "end_line": 3}, "def total(items):\n    return to_cents(sum(items))"),
    ({"file_path": "src/invoice.py", "chunk_type": "function", "name": "bill", "start_line": 1, "end_line": 3}, "def bill(c):\n    return to_cents(total(c)) + d.items()"),
    ({"file_path": "src/coupons.py", "chunk_type": "function", "name": "items", "start_line": 1, "end_line": 2}, "def items():\n    return []"),
    ({"file_path": "tests/test_cart.py", "chunk_type": "function", "name": "test_total", "start_line": 1, "end_line": 2}, "def test_total():\n    total([1]); to_cents(1)"),
    ({"file_path": "src/cart.py", "chunk_type": "overview", "name": "", "start_line": 1, "end_line": 9}, "módulo del carro"),
]


class FakeCollection:
    def get(self, include=None, limit=None):
        return {"metadatas": [m for m, _ in CHUNKS], "documents": [c for _, c in CHUNKS]}


class FakeRetriever:
    def retrieve(self, text, top_k=6):
        name = "to_cents" if "cents" in text else "bill"
        meta = next(m for m, _ in CHUNKS if m["name"] == name)
        return [RetrievedChunk(meta["file_path"], "function", name, 1, 3, "", 0.2)]


def test_reading_order_and_key_functions(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "money.py.md").write_text("# money.py\n\n## Propósito\nConvierte montos a centavos.\n", encoding="utf-8")
    cmap = onboarding.call_map(FakeCollection())
    files = onboarding.reading_order(cmap, tmp_path)
    assert [f["file_path"] for f in files][:2] == ["src/money.py", "src/cart.py"]  # tests do not count
    money = files[0]
    assert money["callers"] == 2 and money["purpose"] == "Convierte montos a centavos."
    assert money["key_functions"] == [{"name": "to_cents", "callers": 2}]
    assert not any(k["name"] == "items" for f in files for k in f["key_functions"])  # generic name ignored


def test_first_issues_by_impact_radius():
    cmap = onboarding.call_map(FakeCollection())
    issues = [{"id": "1", "ref": "#1", "title": "Redondeo de cents", "labels": []},
              {"id": "2", "ref": "#2", "title": "Factura duplicada", "labels": []},
              {"id": "3", "ref": "#3", "title": "Redondeo de cents en la doc", "labels": ["good first issue"]}]
    ranked = onboarding.first_issues(FakeRetriever(), cmap, issues)
    assert [i["ref"] for i in ranked] == ["#3", "#2", "#1"]
    assert ranked[1]["radius"] == "small" and ranked[1]["callers"] == 0 and ranked[2]["callers"] == 2
    assert ranked[0]["easy_label"] and ranked[0]["files"] == ["src/money.py"]


def test_onboarding_endpoint(monkeypatch, tmp_path):
    engine = type("E", (), {"collection": FakeCollection(), "retriever": FakeRetriever()})()
    project = type("P", (), {"repo_path": str(tmp_path), "docs_dir": str(tmp_path / "docs")})()
    prompts = []
    monkeypatch.setattr(api, "_get_engine", lambda pid: engine)
    monkeypatch.setattr(api, "get_project", lambda pid: project)
    monkeypatch.setattr(assist, "_ask_model", lambda eng, system, user, max_tokens=900: prompts.append(user) or "**centavo**: unidad")
    monkeypatch.setattr(assist, "_code_of", lambda eng, name, limit=2500: "")
    client = TestClient(api.app)
    r = client.post("/projects/p/onboarding", json={"issues": [{"id": "2", "ref": "#2", "title": "Factura"}]}).json()
    assert r["files"][0]["file_path"] == "src/money.py" and r["files"][0]["experts"] == []  # not a git repository
    assert r["issues"][0]["ref"] == "#2" and r["glossary"] == "- **centavo**: unidad" and "src/money.py" in prompts[0]
    assert client.post("/projects/p/onboarding", json={"glossary": False}).json()["glossary"] == ""
