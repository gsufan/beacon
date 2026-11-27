"""Knowledge verified by a tech lead: signatures from the Hub, document states, drafts that never overwrite reviewed
text, and verified answers offered for similar questions until the cited code changes."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import git  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from core import verification as V  # noqa: E402
from core.engine.doc_generator import DocGenerator  # noqa: E402
from hub.app import Hub, create_app  # noqa: E402
from hub.security import load_or_create_key, public_key_text, sign_token  # noqa: E402


@pytest.fixture
def key(tmp_path):
    k = load_or_create_key(tmp_path / "k.pem")
    return k, public_key_text(k)


def _token(k, ref, content, kind="doc"):
    return sign_token(k, {"typ": "verification", "org": 1, "project": "api", "kind": kind, "ref": ref,
                          "digest": V.digest(content), "reviewer": "Tania", "iat": 0, "exp": 4_000_000_000})


def test_hub_signs_verifications_only_for_tech_leads(tmp_path):
    hub = Hub(tmp_path / "hub")
    c = TestClient(create_app(hub))
    admin = {"Authorization": f"Bearer {c.post('/signup', json={'org_name': 'Acme', 'name': 'Ana', 'email': 'ana@acme.cl', 'password': 'secreta123'}).json()['session']}"}
    c.post("/projects", json={"id": "api", "name": "API"}, headers=admin)
    people = {}
    for email, role in [("tania@acme.cl", "tech_lead"), ("diego@acme.cl", "developer")]:
        code = c.post("/invitations", json={"email": email}, headers=admin).json()["code"]
        res = c.post("/invitations/accept", json={"code": code, "name": email.split("@")[0].title(), "password": "secreta123"}).json()
        c.put(f"/projects/api/members/{res['user']['sub']}", json={"role": role}, headers=admin)
        people[role] = {"Authorization": f"Bearer {c.post('/auth/login', json={'email': email, 'password': 'secreta123'}).json()['session']}"}
    body = {"kind": "doc", "ref": "auth.py", "digest": V.digest("Documento")}
    assert c.post("/projects/api/verifications", json=body, headers=people["developer"]).status_code == 403
    res = c.post("/projects/api/verifications", json=body, headers=people["tech_lead"])
    assert res.status_code == 200 and res.json()["reviewer"] == "Tania"
    assert c.get("/projects/api/verifications/summary", headers=people["developer"]).json() == {"doc": 1}


def test_document_states_and_drafts(tmp_path, key):
    k, pub = key
    project_dir, docs = tmp_path / "data" / "api", tmp_path / "data" / "api" / "docs"
    (docs / "src").mkdir(parents=True)
    doc = docs / "src" / "auth.py.md"
    doc.write_text("# auth.py\nBorrador de la IA", encoding="utf-8")
    assert V.doc_status(project_dir, docs, "src/auth.py", pub)["status"] == "draft"
    V.record_doc(project_dir, "src/auth.py", _token(k, "src/auth.py", doc.read_text(encoding="utf-8")), "Tania", corrected=False)
    assert V.doc_status(project_dir, docs, "src/auth.py", pub)["status"] == "verified"
    doc.write_text("# auth.py\nTexto cambiado sin verificar", encoding="utf-8")
    assert V.doc_status(project_dir, docs, "src/auth.py", pub)["status"] == "pending"
    # A forged signature (another key) is not accepted.
    other = load_or_create_key(tmp_path / "otra.pem")
    V.record_doc(project_dir, "src/auth.py", _token(other, "src/auth.py", doc.read_text(encoding="utf-8")), "Intruso", corrected=False)
    assert V.doc_status(project_dir, docs, "src/auth.py", pub)["status"] == "pending"


def test_new_ai_draft_never_overwrites_a_reviewed_document(tmp_path, key):
    k, _ = key
    project_dir = tmp_path / "data" / "api"
    docs = project_dir / "docs"
    docs.mkdir(parents=True)

    class FakeProject:
        docs_dir = str(docs)

    gen = DocGenerator.__new__(DocGenerator)
    gen.project = FakeProject()
    gen.write_doc("auth.py", "Primera versión de la IA")
    assert (docs / "auth.py.md").read_text(encoding="utf-8") == "Primera versión de la IA"
    V.record_doc(project_dir, "auth.py", _token(k, "auth.py", "Corregido por Tania"), "Tania", corrected=True)
    (docs / "auth.py.md").write_text("Corregido por Tania", encoding="utf-8")
    gen.write_doc("auth.py", "Nueva versión de la IA tras un cambio en el código")
    assert (docs / "auth.py.md").read_text(encoding="utf-8") == "Corregido por Tania"
    assert (docs / "auth.py.draft.md").read_text(encoding="utf-8").startswith("Nueva versión")


def test_verified_answers_match_similar_questions_and_detect_code_changes(tmp_path, key):
    k, pub = key
    repo = git.Repo.init(tmp_path / "repo")
    (tmp_path / "repo" / "auth.py").write_text("def login(): pass\n", encoding="utf-8")
    repo.index.add(["auth.py"])
    repo.index.commit("inicial")
    project_dir = tmp_path / "data" / "api"
    answer = "El inicio de sesión está en auth.login."
    V.add_answer(project_dir, str(tmp_path / "repo"), "¿Dónde está el login?", answer,
                 [{"file_path": "auth.py", "chunk_type": "function"}], [1.0, 0.0, 0.0], _token(k, "a1", answer, "answer"),
                 "Tania", corrected=True, answer_id="a1")
    m = V.match(project_dir, str(tmp_path / "repo"), [0.99, 0.05, 0.0], pub)
    assert m and m["answer"] == answer and m["reviewer"] == "Tania" and m["stale_files"] == []
    assert V.match(project_dir, str(tmp_path / "repo"), [0.0, 1.0, 0.0], pub) is None  # a different question
    (tmp_path / "repo" / "auth.py").write_text("def login(user): pass\n", encoding="utf-8")
    repo.index.add(["auth.py"])
    repo.index.commit("cambio")
    assert V.match(project_dir, str(tmp_path / "repo"), [1.0, 0.0, 0.0], pub)["stale_files"] == ["auth.py"]
    assert V.remove_answer(project_dir, "a1") and V.list_answers(project_dir, str(tmp_path / "repo"), pub) == []
