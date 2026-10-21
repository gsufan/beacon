"""Agregación de resultados del estudio: validación, duplicados y resumen por VRAM."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from core.study.session import StudySession  # noqa: E402
import study_analysis  # noqa: E402


def _result(tmp_path, name, vram, seconds, question="q"):
    s = StudySession(tmp_path / f"{name}.sesion")
    s.start(profile="abierto")
    s.consent("participacion", True)
    for i in range(3):
        n = s.add_question(f"{question} {i}", "r", seconds)
        s.rate(n, "si" if i else "no", useful_sources=4)
    if vram:
        s.consent("hardware", True)
        s.set_hardware({"gpus": [{"modelo": "g", "vram_gb": vram}]})
    return s.build_result()


def test_summary_groups_by_vram_and_rejects_bad_and_duplicate_files(tmp_path):
    folder = tmp_path / "r"
    folder.mkdir()
    (folder / "a.json").write_text(json.dumps(_result(tmp_path, "a", 8, 4.0)))
    (folder / "b.json").write_text(json.dumps(_result(tmp_path, "b", 6, 10.0, "otra")))
    (folder / "dup.json").write_text(json.dumps(_result(tmp_path, "d", 8, 4.0)))  # mismas preguntas que a
    (folder / "roto.json").write_text("{no es json")
    (folder / "x-identidad.json").write_text('{"github": "no debe leerse"}')
    out = study_analysis.summarize(study_analysis.load_files(folder))
    assert out["archivos"] == 4 and out["validos"] == 2 and out["preguntas"] == 6
    assert {r[0] for r in out["rechazados"]} == {"dup.json", "roto.json"}
    assert out["tiempo_por_vram"]["7-8 GB"]["mediana"] == 4.0 and out["tiempo_por_vram"]["<=6 GB"]["mediana"] == 10.0
    assert out["correctas"]["si"] == 66.7


def test_csv_column_is_read(tmp_path):
    csv_path = tmp_path / "f.csv"
    import csv
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Marca", "Resultados"])
        w.writerow(["x", json.dumps(_result(tmp_path, "c", None, 3.0))])
    out = study_analysis.summarize(study_analysis.load_csv(csv_path, "Resultados"))
    assert out["validos"] == 1 and "sin GPU / no declarada" in out["tiempo_por_vram"]
