"""Modo estudio: consentimientos, datos del equipo, sesión, archivo de resultados y validación."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402

from core.study import hardware  # noqa: E402
from core.study.session import MIN_QUESTIONS, StudyError, StudySession  # noqa: E402
from core.study.validate import validate_result  # noqa: E402


@pytest.fixture
def session(tmp_path):
    s = StudySession(tmp_path / "_study" / "sesion.json")
    s.start(profile="abierto", language="es", beacon_version="0.1", beacon_commit="abc1234")
    return s


def _complete(s, questions=3, rate=True):
    s.consent("participacion", True)
    for i in range(questions):
        n = s.add_question(f"pregunta {i}", f"respuesta {i}", 5.0 + i, 100, 50,
                           [{"archivo": "src/a.py", "start_line": 1, "end_line": 9, "code": "SECRETO"}])
        if rate:
            s.rate(n, "si", useful_sources=4, solved_alone=True)


# ---- consentimientos -------------------------------------------------------

def test_hardware_cannot_be_stored_without_consent(session):
    with pytest.raises(StudyError):
        session.set_hardware({"cpu_modelo": "x"})
    session.consent("hardware", True)
    session.set_hardware({"cpu_modelo": "x"}, ollama_version="0.3", models=["m"])
    assert session.state["hardware"]["modelos"] == ["m"]


def test_withdrawing_hardware_consent_deletes_the_data(session):
    session.consent("hardware", True)
    session.set_hardware({"cpu_modelo": "x"})
    session.consent("hardware", False)
    assert session.state["hardware"] is None
    assert not session.has_consent("hardware")


def test_unknown_consent_kind_and_profile_are_rejected(tmp_path, session):
    with pytest.raises(StudyError):
        session.consent("otra-cosa", True)
    with pytest.raises(StudyError):
        StudySession(tmp_path / "x.json").start(profile="raro")
    with pytest.raises(StudyError):
        StudySession(tmp_path / "y.json").start(profile="piloto")  # el piloto exige código


# ---- resultados ------------------------------------------------------------

def test_result_requires_consent_and_minimum_questions(session):
    with pytest.raises(StudyError):
        session.build_result()
    _complete(session, questions=MIN_QUESTIONS - 1)
    assert not session.can_finish()
    session.add_question("otra", "resp", 4.0)
    assert session.can_finish()
    assert session.build_result()["esquema"] == 1


def test_result_never_contains_source_code_or_absolute_paths(session):
    session.consent("participacion", True)
    for i in range(3):
        session.add_question("p", "r", 3.0, sources=[
            {"archivo": "C:\\Users\\persona\\proyecto\\a.py", "start_line": 1, "end_line": 5, "code": "CODIGO SECRETO"},
            {"archivo": "/home/persona/b.py", "lineas": "7-9", "code": "OTRO"}])
    text = json.dumps(session.build_result(), ensure_ascii=False)
    assert "SECRETO" not in text and "OTRO" not in text
    assert "persona" not in text and "Users" not in text
    assert {"archivo": "a.py", "lineas": "1-5"} in session.state["preguntas"][0]["fuentes"]


def test_identity_is_kept_out_of_the_results_file(session):
    _complete(session)
    session.set_identity(github="https://github.com/alguien", presentation="Hola")
    assert "alguien" not in json.dumps(session.build_result())
    assert session.build_identity()["github"] == "https://github.com/alguien"


def test_rating_validation(session):
    session.consent("participacion", True)
    n = session.add_question("p", "r", 2.0)
    with pytest.raises(StudyError):
        session.rate(n, "quizas")
    with pytest.raises(StudyError):
        session.rate(n, "si", useful_sources=9)
    with pytest.raises(StudyError):
        session.rate(99, "si")


def test_session_survives_a_restart_and_can_be_discarded(tmp_path):
    path = tmp_path / "s.json"
    s = StudySession(path)
    s.start(profile="piloto", code="P3", order=2)
    s.add_question("p", "r", 1.5)
    again = StudySession(path)  # como si se cerrara y abriera de nuevo
    assert again.state["meta"]["codigo"] == "P3" and len(again.state["preguntas"]) == 1
    again.discard()
    assert not path.exists() and not StudySession(path).started


def test_pilot_task_condition_is_checked(tmp_path):
    s = StudySession(tmp_path / "p.json")
    s.start(profile="piloto", code="P1", order=1)
    s.add_pilot_task("A1", "con", 300, False, "ninguna", "explicación")
    with pytest.raises(StudyError):
        s.add_pilot_task("A2", "tal vez", 10, False, "", "")


# ---- validación automática ---------------------------------------------------

def test_a_good_file_passes_validation(session):
    _complete(session)
    problems, warnings = validate_result(session.build_result())
    assert problems == [] and warnings == []


def test_validation_reports_problems_and_suspicious_files(session):
    _complete(session)
    data = session.build_result()
    data_bad = dict(data, preguntas=data["preguntas"][:1])
    assert "menos de 3 preguntas" in validate_result(data_bad)[0]
    assert validate_result({"esquema": 1})[0]  # faltan campos
    assert validate_result([])[0]

    suspicious = json.loads(json.dumps(data))
    suspicious["hardware"] = {"gpus": [{"modelo": "g", "vram_gb": 8}]}
    for q in suspicious["preguntas"]:
        q["segundos_respuesta"] = 0.1
    warnings = validate_result(suspicious)[1]
    assert any("imposible" in w for w in warnings) and any("GPU" in w for w in warnings)

    repeated = json.loads(json.dumps(data))
    for q in repeated["preguntas"]:
        q["pregunta"] = "igual"
    assert "preguntas repetidas" in validate_result(repeated)[1]


# ---- datos del equipo ----------------------------------------------------------

def test_nvidia_smi_output_is_parsed():
    out = "NVIDIA GeForce RTX 5070 Laptop GPU, 8151\nNVIDIA GeForce GTX 1660 Ti, 6144\nlinea rara"
    assert hardware.parse_nvidia_smi(out) == [
        {"modelo": "NVIDIA GeForce RTX 5070 Laptop GPU", "vram_gb": 8.0},
        {"modelo": "NVIDIA GeForce GTX 1660 Ti", "vram_gb": 6.0}]


def test_hardware_snapshot_has_only_the_expected_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(hardware, "_gpus", lambda: [])
    snapshot = hardware.collect_hardware(str(tmp_path))
    assert set(snapshot) == {"cpu_modelo", "cpu_nucleos", "ram_gb", "disco_libre_gb", "gpus", "so"}
    assert snapshot["gpus"] == [] and snapshot["cpu_nucleos"]
    assert str(tmp_path) not in json.dumps(snapshot)  # la ruta no se guarda
