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
    ai_config = SimpleNamespace(host="http://x")

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


class _Collector:
    """Minimal collector on this machine: records what it receives, like tools/study_collector.gs would."""

    def __init__(self, reply=b'{"ok": true, "recibo": "abc123"}', status=200):
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer
        received, outer = [], self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                received.append(self.rfile.read(int(self.headers["Content-Length"])))
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(reply)

            def log_message(self, *args):
                pass

        self.received = received
        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


def _finished(client, tmp_path):
    _ready(client, tmp_path)
    for i in range(3):
        n = client.post("/study/ask", json={"pregunta": f"pregunta {i}"}).json()["n"]
        client.post("/study/rate", json={"n": n, "correcta": "si", "fuentes_utiles": 4})
    client.post("/study/consent", json={"tipo": "envio", "aceptado": True})
    client.post("/study/identity", json={"github": "https://github.com/alguien", "presentacion": ""})


def test_submit_is_unavailable_without_a_destination(client, tmp_path, monkeypatch):
    monkeypatch.delenv("BEACON_STUDY_SUBMIT_URL", raising=False)
    monkeypatch.setattr(study_api.submit, "DEFAULT_SUBMIT_URL", "")
    _finished(client, tmp_path)
    assert client.get("/study/state").json()["envio"]["disponible"] is False
    assert client.post("/study/submit", json={}).status_code == 409


def test_submit_sends_the_results_and_records_the_receipt(client, tmp_path, monkeypatch):
    collector = _Collector()
    try:
        monkeypatch.setenv("BEACON_STUDY_SUBMIT_URL", collector.url)
        _finished(client, tmp_path)
        assert client.get("/study/state").json()["envio"] == {"disponible": True, "destino": "127.0.0.1", "enviado": None}
        out = client.post("/study/submit", json={"con_identidad": False})
        assert out.status_code == 200 and out.json()["recibo"] == "abc123"
        body = json.loads(collector.received[0])
        assert len(body["resultado"]["preguntas"]) == 3 and body["identidad"] is None
        assert "alguien" not in collector.received[0].decode("utf-8")  # identity only travels if asked for
        assert client.get("/study/state").json()["envio"]["enviado"]["recibo"] == "abc123"
    finally:
        collector.close()


def test_submit_includes_identity_only_when_requested(client, tmp_path, monkeypatch):
    collector = _Collector()
    try:
        monkeypatch.setenv("BEACON_STUDY_SUBMIT_URL", collector.url)
        _finished(client, tmp_path)
        assert client.post("/study/submit", json={"con_identidad": True}).status_code == 200
        body = json.loads(collector.received[0])
        assert body["identidad"]["github"].endswith("alguien") and body["identidad"]["id"] == body["resultado"]["meta"]["id"]
    finally:
        collector.close()


def test_submit_needs_consent_and_reports_collector_failures(client, tmp_path, monkeypatch):
    collector = _Collector(reply=b'{"ok": false, "error": "tama\xc3\xb1o inv\xc3\xa1lido"}')
    try:
        monkeypatch.setenv("BEACON_STUDY_SUBMIT_URL", collector.url)
        _ready(client, tmp_path)
        assert client.post("/study/submit", json={}).status_code == 409  # not authorized yet
        client.post("/study/consent", json={"tipo": "envio", "aceptado": True})
        assert client.post("/study/submit", json={}).status_code == 409  # fewer than the minimum questions
        for i in range(3):
            n = client.post("/study/ask", json={"pregunta": f"pregunta {i}"}).json()["n"]
            client.post("/study/rate", json={"n": n, "correcta": "no"})
        failed = client.post("/study/submit", json={})
        assert failed.status_code == 502 and "rechazó" in failed.json()["detail"]
        assert client.get("/study/state").json()["envio"]["enviado"] is None
    finally:
        collector.close()


def test_submit_reports_an_unreachable_collector(client, tmp_path, monkeypatch):
    monkeypatch.setenv("BEACON_STUDY_SUBMIT_URL", "http://127.0.0.1:9/")
    _finished(client, tmp_path)
    failed = client.post("/study/submit", json={})
    assert failed.status_code == 502 and "conectar" in failed.json()["detail"]


def test_submit_url_only_accepts_https_or_local_http(monkeypatch):
    for url, ok in (("https://script.google.com/macros/s/x/exec", True), ("http://127.0.0.1:8001/", True),
                    ("http://example.com/", False), ("ftp://example.com/", False), ("", False)):
        monkeypatch.setenv("BEACON_STUDY_SUBMIT_URL", url)
        assert bool(study_api.submit.submit_url()) is ok, url


def test_cleanup_removes_orphan_study_clones_but_not_other_folders(tmp_path, monkeypatch):
    from core import cli
    monkeypatch.setattr(cli, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(study_api, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(cli, "load_config", lambda: SimpleNamespace(projects=[SimpleNamespace(id="estudio-ffff")]))
    for name in ("estudio", "estudio-ab12", "estudio-cd34", "estudio-ffff", "estudio-notes", "requests"):
        (tmp_path / name).mkdir()
    cli._remove_orphan_study_clones(keep=("estudio-cd34",))
    left = sorted(p.name for p in tmp_path.iterdir())
    assert left == ["estudio", "estudio-cd34", "estudio-ffff", "estudio-notes", "requests"]  # the kept one, the user's own and unrelated folders
    cli._remove_study_data()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["estudio-ffff", "estudio-notes", "requests"]
