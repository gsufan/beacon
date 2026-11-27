"""Team insights: impact radius from the index, knowledge map and bus factor from git, code linked to an issue,
sprint summary and the anonymous usage behind documentation gaps and the radar of hard code."""
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import git  # noqa: E402
import pytest  # noqa: E402

from core import insight  # noqa: E402


class FakeCollection:
    def __init__(self, chunks):
        self.chunks = chunks  # id -> (metadata, code)

    def get(self, where=None, where_document=None, include=None):
        ids, metas, docs = [], [], []
        for cid, (meta, code) in self.chunks.items():
            if where and any(meta.get(k) != v for k, v in where.items()):
                continue
            if where_document and where_document["$contains"] not in code:
                continue
            ids.append(cid)
            metas.append(meta)
            docs.append(code)
        return {"ids": ids, "metadatas": metas, "documents": docs}


def _chunk(name, path, code, kind="function"):
    return {"name": name, "file_path": path, "start_line": 1, "end_line": 5, "chunk_type": kind}, code


def test_impact_finds_direct_and_indirect_callers_and_tests():
    col = FakeCollection({
        "a": _chunk("parse_header", "src/utils.py", "def parse_header(v):\n    return v.strip()"),
        "b": _chunk("build_auth", "src/auth.py", "def build_auth(h):\n    return parse_header(h)"),
        "c": _chunk("request", "src/api.py", "def request():\n    build_auth(x)"),
        "d": _chunk("test_parse", "tests/test_utils.py", "def test_parse():\n    assert parse_header(' a ') == 'a'"),
        "e": _chunk("unrelated", "src/x.py", "def unrelated():\n    my_parse_header(1)"),
    })
    r = insight.impact(col, ["parse_header"])
    t = r["targets"][0]
    assert [c["name"] for c in t["callers"]] == ["build_auth"]
    assert [c["name"] for c in t["indirect"]] == ["request"] and t["indirect"][0]["via"] == "build_auth"
    assert [c["name"] for c in t["tests"]] == ["test_parse"] and t["untested"] is False
    assert "src/x.py" not in r["files"]  # my_parse_header is a different function


def test_bus_factor():
    assert insight.bus_factor(Counter({"ana": 9, "luis": 1})) == 1
    assert insight.bus_factor(Counter({"ana": 5, "luis": 4, "eva": 3})) == 2


@pytest.fixture
def repo(tmp_path):
    r = git.Repo.init(tmp_path)
    (tmp_path / "src" / "core").mkdir(parents=True)
    (tmp_path / "tests").mkdir()
    steps = [("Ana", "src/core/a.py", "x = 1", "feat: base (#3)"), ("Ana", "src/core/a.py", "x = 2", "fix: corrige x #3"),
             ("Luis", "tests/test_a.py", "def test(): pass", "test: agrega prueba (BEAC-7)"), ("Ana", "src/core/b.py", "y = 1", "refactor: b")]
    for author, path, content, msg in steps:
        (tmp_path / path).write_text(content + "\n")
        r.index.add([path])
        r.index.commit(msg, author=git.Actor(author, f"{author.lower()}@acme.cl"))
    return tmp_path


def test_knowledge_map_flags_modules_with_a_single_expert(repo):
    km = insight.knowledge_map(str(repo))
    core = next(m for m in km["modules"] if m["module"] == "src/core")
    assert core["bus_factor"] == 1 and core["risk"] == "alto" and core["experts"][0]["name"] == "Ana"
    assert km["people"] == 2


def test_issue_code_and_sprint_summary(repo):
    code = insight.issue_code(str(repo), "3")
    assert len(code["commits"]) == 2 and code["files"][0] == {"path": "src/core/a.py", "commits": 2}
    assert insight.issue_code(str(repo), "BEAC-7")["files"][0]["path"] == "tests/test_a.py"
    s = insight.sprint_summary(str(repo), days=30)
    assert s["commits"] == 4 and s["issues"][0]["issue"] == "#3" and len(s["issues"][0]["commits"]) == 2
    assert any(g["issue"] == "(sin issue)" for g in s["issues"])


def test_usage_feeds_documentation_gaps_and_the_radar(tmp_path):
    src = [{"file_path": "src/core/a.py", "chunk_type": "function"}]
    insight.record_usage(tmp_path, src, "La función a hace x.")
    insight.record_usage(tmp_path, src, "No hay información suficiente en el código para responder.")
    insight.record_usage(tmp_path, [{"file_path": "src/web/v.py", "chunk_type": "function"}], "Responde.")
    data = insight.insights(tmp_path, tmp_path / "docs", {"src/core/a.py": "draft", "src/core/b.py": "verified"})
    assert data["questions"] == 3 and data["no_info"] == 1
    assert data["radar"][0]["module"] == "src/core" and data["radar"][0]["no_info_rate"] == 0.5
    assert data["gaps"][0]["module"] == "src/core" and data["gaps"][0]["verified_rate"] == 0.5
    assert "La función" not in (tmp_path / "usage.json").read_text(encoding="utf-8")  # no question or answer text is stored


def test_related_issues_are_a_suggestion_above_the_threshold():
    from types import SimpleNamespace

    from core import assist

    class Backend:
        def embed(self, model, texts, num_ctx=None):
            assert not any(t.startswith("Instruct:") for t in texts)  # issue to issue: no question-to-code prefix
            return [[1.0, 0.0] if "digest" in t else [0.0, 1.0] for t in texts]

    engine = SimpleNamespace(backend=Backend(), ai_config=SimpleNamespace(embedding_model="qwen3-embedding:0.6b"))
    issue = {"id": "1", "ref": "#1", "title": "digest auth falla"}
    out = assist.related_issues(engine, issue, [issue, {"id": "2", "ref": "#2", "title": "digest con espacios"},
                                                {"id": "3", "ref": "#3", "title": "timeout en docs"}])
    assert [r["ref"] for r in out] == ["#2"] and out[0]["similarity"] == 1.0
