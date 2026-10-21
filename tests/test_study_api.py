"""Endpoints /study/*: consentimientos, preguntas medidas por el servidor, valoración y archivo final."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from core.projects import InvalidRepoUrlError  # noqa: E402
from core.study import api as study_api  # noqa: E402
from core.study.session import StudySession  # noqa: E402


class FakeEngine:
    ai_config = SimpleNamespace(ollama_host="http://x")

    def ask(self, question, top_k=5, language="es", **_):
        source = SimpleNamespace(file_path="src/a.py", chunk_type="function", name="f", start_line=3, end_line=9,
                                 distance=0.2, expanded=False, code="CODIGO PRIVADO")
        return SimpleNamespace(prompt_tokens=120, answer_tokens=40, to_dict=lambda: {
            "answer": f"respuesta a {question}",
            "sources": [{"file_path": "src/a.py", "chunk_type": "function", "name": "f", "start_line": 3, "end_line": 9,
                         "distance": 0.2, "expanded": False, "code": "CODIGO PRIVADO"}]})


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(study_api, "session_path", lambda: tmp_path / "_study" / "sesion.json")
    monkeypatch.setattr(study_api, "STUDY_PROJECT_ID", "estudio")
    monkeypatch.setattr(study_api, "output_dir", lambda: tmp_path)
    monkeypatch.setattr(study_api.hardware, "collect_hardware", lambda path=".": {
        "cpu_modelo": "cpu", "cpu_nucleos": 4, "ram_gb": 16.0, "disco_libre_gb": 50.0, "gpus": [], "so": "Test"})
    app = FastAPI()
    app.include_router(study_api.build_router(lambda pid: FakeEngine(), lambda: None))
    return TestClient(app)


def _ready(client, tmp_path):
    """Sesión iniciada, participación aceptada y un repositorio listo (se marca directamente)."""
    client.post("/study/start", json={"perfil": "abierto"})
    client.post("/study/consent", json={"tipo": "participacion", "aceptado": True})
    StudySession(tmp_path / "_study" / "sesion.json").set_repo("https://github.com/u/r", "abc", 10, 50, ["python"], 5.0)


def test_state_before_and_after_starting(client):
    assert client.get("/study/state").json() == {"started": False}
    assert client.post("/study/start", json={"perfil": "abierto", "idioma": "en"}).json() == {"started": True}
    state = client.get("/study/state").json()
    assert state["started"] and state["meta"]["idioma"] == "en" and not state["puede_terminar"]


def test_pilot_profile_requires_a_code(client):
    assert client.post("/study/start", json={"perfil": "piloto"}).status_code == 400
    assert client.post("/study/start", json={"perfil": "piloto", "codigo": "P2", "orden": 2}).status_code == 200


def test_hardware_is_previewed_but_only_saved_with_consent(client):
    client.post("/study/start", json={})
    assert client.get("/study/hardware-preview").json()["cpu_modelo"] == "cpu"
    assert client.get("/study/state").json()["hardware"] is None  # ver no es guardar
    assert client.post("/study/hardware").status_code == 400  # sin consentimiento
    client.post("/study/consent", json={"tipo": "hardware", "aceptado": True})
    assert client.post("/study/hardware").json()["cpu_modelo"] == "cpu"
    client.post("/study/consent", json={"tipo": "hardware", "aceptado": False})
    assert client.get("/study/state").json()["hardware"] is None  # retirarlo lo borra


def test_asking_needs_consent_and_a_ready_repository(client, tmp_path):
    client.post("/study/start", json={})
    assert client.post("/study/ask", json={"pregunta": "¿qué hace f?"}).status_code == 409  # sin consentimiento
    client.post("/study/consent", json={"tipo": "participacion", "aceptado": True})
    assert client.post("/study/ask", json={"pregunta": "¿qué hace f?"}).status_code == 409  # sin repositorio
    StudySession(tmp_path / "_study" / "sesion.json").set_repo("https://github.com/u/r", "abc", 10, 50, ["python"], 5.0)
    out = client.post("/study/ask", json={"pregunta": "¿qué hace f?"}).json()
    assert out["n"] == 1 and out["segundos"] >= 0 and out["respuesta"].startswith("respuesta")


def test_questions_are_recorded_without_source_code(client, tmp_path):
    _ready(client, tmp_path)
    client.post("/study/ask", json={"pregunta": "primera pregunta"})
    saved = json.dumps(StudySession(tmp_path / "_study" / "sesion.json").state)
    assert "CODIGO PRIVADO" not in saved and '"lineas": "3-9"' in saved


def test_rating_and_final_file_flow(client, tmp_path):
    _ready(client, tmp_path)
    for i in range(3):
        n = client.post("/study/ask", json={"pregunta": f"pregunta {i}"}).json()["n"]
        assert client.post("/study/rate", json={"n": n, "correcta": "si", "fuentes_utiles": 5}).status_code == 200
    assert client.post("/study/rate", json={"n": 1, "correcta": "quizas"}).status_code == 400
    assert client.get("/study/result").status_code == 409  # falta autorizar la generación
    client.post("/study/consent", json={"tipo": "envio", "aceptado": True})
    client.post("/study/identity", json={"github": "https://github.com/alguien", "presentacion": "hola"})
    out = client.get("/study/result").json()
    assert out["resultado"]["esquema"] == 1 and len(out["resultado"]["preguntas"]) == 3
    assert "alguien" not in json.dumps(out["resultado"]) and out["identidad"]["github"].endswith("alguien")
    assert out["archivos"] == [study_api.RESULT_FILE, study_api.IDENTITY_FILE]
    assert json.loads((tmp_path / study_api.RESULT_FILE).read_text(encoding="utf-8"))["esquema"] == 1
    assert "alguien" not in (tmp_path / study_api.RESULT_FILE).read_text(encoding="utf-8")


def test_result_needs_the_minimum_number_of_questions(client, tmp_path):
    _ready(client, tmp_path)
    client.post("/study/ask", json={"pregunta": "una sola"})
    client.post("/study/consent", json={"tipo": "envio", "aceptado": True})
    assert client.get("/study/result").status_code == 409


def test_discard_removes_the_session(client, tmp_path, monkeypatch):
    monkeypatch.setattr(study_api.services, "unregister_project", lambda *a, **k: None)
    _ready(client, tmp_path)
    assert client.post("/study/discard").json() == {"ok": True}
    assert client.get("/study/state").json() == {"started": False}


def test_only_public_https_repositories_are_accepted(client):
    client.post("/study/start", json={})
    for bad in ("git@github.com:u/r.git", "ext::sh -c touch /tmp/x", "http://github.com/u/r", "https://github.com/solo-usuario",
                "https://sitio-raro.example/u/r"):
        assert client.post("/study/repo", json={"url": bad}).status_code == 400, bad
    assert study_api.check_public_repo_url("https://github.com/u/r") == "https://github.com/u/r"
    with pytest.raises(InvalidRepoUrlError):
        study_api.check_public_repo_url("https://file.example/u/r")


def test_experience_is_stored(client):
    client.post("/study/start", json={})
    assert client.post("/study/experience", json={"datos": {"anios": 4, "likert": {"util": 5}}}).status_code == 200
    assert client.get("/study/state").json()["experiencia"]["anios"] == 4
