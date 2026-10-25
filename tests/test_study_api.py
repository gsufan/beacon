"""Endpoints /study/*: consents, questions timed by the server, rating and final file."""
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
    """Session started, participation accepted and a repository ready (marked directly)."""
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
    assert client.get("/study/state").json()["hardware"] is None  # viewing is not saving
    assert client.post("/study/hardware").status_code == 400  # without consent
    client.post("/study/consent", json={"tipo": "hardware", "aceptado": True})
    assert client.post("/study/hardware").json()["cpu_modelo"] == "cpu"
    client.post("/study/consent", json={"tipo": "hardware", "aceptado": False})
    assert client.get("/study/state").json()["hardware"] is None  # withdrawing it deletes it


def test_asking_needs_consent_and_a_ready_repository(client, tmp_path):
    client.post("/study/start", json={})
    assert client.post("/study/ask", json={"pregunta": "¿qué hace f?"}).status_code == 409  # without consent
    client.post("/study/consent", json={"tipo": "participacion", "aceptado": True})
    assert client.post("/study/ask", json={"pregunta": "¿qué hace f?"}).status_code == 409  # without a repository
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
    assert client.get("/study/result").status_code == 409  # submission generation not authorized yet
    client.post("/study/consent", json={"tipo": "envio", "aceptado": True})
    client.post("/study/identity", json={"github": "https://github.com/alguien", "presentacion": "hola"})
    out = client.get("/study/result").json()
    assert out["resultado"]["esquema"] == 1 and len(out["resultado"]["preguntas"]) == 3
    assert "alguien" not in json.dumps(out["resultado"]) and out["identidad"]["github"].endswith("alguien")
    sid = out["resultado"]["meta"]["id"]
    assert len(sid) == 6 and out["identidad"]["id"] == sid  # the short id links the two files
    assert out["archivos"] == [study_api.result_file(sid), study_api.identity_file(sid)]
    text = (tmp_path / study_api.result_file(sid)).read_text(encoding="utf-8")
    assert json.loads(text)["esquema"] == 1 and "alguien" not in text
    assert json.loads((tmp_path / study_api.identity_file(sid)).read_text(encoding="utf-8"))["id"] == sid


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


def test_repository_cannot_change_once_questions_were_asked(client, tmp_path):
    _ready(client, tmp_path)
    client.post("/study/ask", json={"pregunta": "una pregunta"})
    assert client.post("/study/repo", json={"url": "https://github.com/u/otro"}).status_code == 409


def test_experience_is_stored(client):
    client.post("/study/start", json={})
    assert client.post("/study/experience", json={"datos": {"anios": 4, "likert": {"util": 5}}}).status_code == 200
    assert client.get("/study/state").json()["experiencia"]["anios"] == 4


def test_discard_also_removes_the_cloned_repository_folder(client, tmp_path, monkeypatch):
    monkeypatch.setattr(study_api.services, "unregister_project", lambda *a, **k: None)
    monkeypatch.setattr(study_api, "DATA_ROOT", tmp_path)
    (tmp_path / "estudio" / "repo").mkdir(parents=True)
    _ready(client, tmp_path)
    assert client.get("/study/state").json()["carpeta_repo"] == str(tmp_path / "estudio")
    client.post("/study/discard")
    assert not (tmp_path / "estudio").exists()


def test_quit_discards_and_schedules_the_shutdown(client, tmp_path, monkeypatch):
    monkeypatch.setattr(study_api.services, "unregister_project", lambda *a, **k: None)
    monkeypatch.setattr(study_api, "DATA_ROOT", tmp_path)
    timers = []
    monkeypatch.setattr(study_api.threading, "Timer", lambda delay, fn: type("T", (), {"start": lambda self: timers.append(delay)})())
    _ready(client, tmp_path)
    assert client.post("/study/quit").json() == {"ok": True}
    assert client.get("/study/state").json() == {"started": False} and timers == [0.7]


def test_each_repository_gets_its_own_folder_and_the_previous_ones_are_removed(client, tmp_path, monkeypatch):
    import yaml
    monkeypatch.setattr(study_api, "DATA_ROOT", tmp_path)
    (tmp_path / "_study").mkdir(exist_ok=True)
    (tmp_path / "_study" / "config.yaml").write_text(yaml.safe_dump({"projects": [
        {"id": "estudio-aaaa"}, {"id": "otro-proyecto"}, {"id": "estudio-bbbb"}]}), encoding="utf-8")
    for name in ("estudio-aaaa", "estudio-bbbb", "otro-proyecto"):
        (tmp_path / name).mkdir()
    monkeypatch.setattr(study_api.services, "unregister_project", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("en uso")))
    assert study_api.study_project_ids() == ["estudio-aaaa", "estudio-bbbb"]  # only the study ones
    assert study_api.current_project_id() == "estudio-bbbb"  # the most recent
    study_api.remove_study_repos(keep="estudio-bbbb")  # even if unregister fails, it does not crash and cleans up what it can
    assert not (tmp_path / "estudio-aaaa").exists() and (tmp_path / "estudio-bbbb").exists() and (tmp_path / "otro-proyecto").exists()
