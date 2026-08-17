"""Tests de 'beacon export'/'beacon import' con datos temporales (no toca config.yaml real)."""

import json
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from typer.testing import CliRunner  # noqa: E402

import core.cli as cli_module  # noqa: E402
from core.config import AppConfig, AIProviderConfig, ProjectEntry  # noqa: E402

runner = CliRunner()

FAKE_AI_CONFIG = AIProviderConfig(
    provider="ollama", ollama_host="http://localhost:11434",
    embedding_model="nomic-embed-text", llm_model="llama3:8b",
)


def _fake_entry(project_id="demo-export"):
    return ProjectEntry(id=project_id, name="Demo Export", repo_path="/tmp/demo-export", source_type="local")


def test_export_missing_project_in_config_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(cli_module, "load_config", lambda: AppConfig(ai_provider=FAKE_AI_CONFIG, projects=[]))
    result = runner.invoke(cli_module.app, ["export", "no-such-project"])
    assert result.exit_code == 1


def test_export_missing_data_dir_fails(monkeypatch, tmp_path):
    entry = _fake_entry()
    monkeypatch.setattr(cli_module, "load_config", lambda: AppConfig(ai_provider=FAKE_AI_CONFIG, projects=[entry]))
    monkeypatch.setattr(cli_module, "DATA_ROOT", tmp_path / "data")  # no existe
    result = runner.invoke(cli_module.app, ["export", entry.id])
    assert result.exit_code == 1


def test_export_then_import_round_trip(monkeypatch, tmp_path):
    entry = _fake_entry("demo-roundtrip")
    data_root = tmp_path / "data"
    project_dir = data_root / entry.id / "chroma_db"
    project_dir.mkdir(parents=True)
    (project_dir / "fake_index_file.bin").write_text("contenido de prueba")
    (data_root / entry.id / "docs").mkdir(parents=True)
    (data_root / entry.id / "docs" / "a.py.md").write_text("# doc de prueba")

    monkeypatch.setattr(cli_module, "load_config", lambda: AppConfig(ai_provider=FAKE_AI_CONFIG, projects=[entry]))
    monkeypatch.setattr(cli_module, "DATA_ROOT", data_root)

    output_zip = tmp_path / "export.zip"
    result = runner.invoke(cli_module.app, ["export", entry.id, "--output", str(output_zip)])
    assert result.exit_code == 0, result.output
    assert output_zip.exists()

    with zipfile.ZipFile(output_zip) as zf:
        manifest = json.loads(zf.read("manifest.json"))
        assert manifest["id"] == entry.id
        assert "data/chroma_db/fake_index_file.bin" in zf.namelist()

    # --- import a un DATA_ROOT nuevo, simulando otra máquina ---
    new_data_root = tmp_path / "data-imported"
    monkeypatch.setattr(cli_module, "DATA_ROOT", new_data_root)

    added = {}

    def fake_add_project(entry_obj):
        added["entry"] = entry_obj
        return entry_obj

    monkeypatch.setattr(cli_module, "add_project", fake_add_project)

    result = runner.invoke(cli_module.app, ["import", str(output_zip)])
    assert result.exit_code == 0, result.output
    assert added["entry"].id == entry.id
    assert (new_data_root / entry.id / "chroma_db" / "fake_index_file.bin").read_text() == "contenido de prueba"
    assert (new_data_root / entry.id / "docs" / "a.py.md").exists()


def test_import_rejects_zip_slip(monkeypatch, tmp_path):
    """Un .zip manipulado con rutas '../' no debe poder escribir fuera de DATA_ROOT/<id>/."""
    evil_zip = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil_zip, "w") as zf:
        zf.writestr("manifest.json", json.dumps({
            "id": "evil-project", "name": "Evil", "repo_path": "/tmp/evil",
            "source_type": "local", "repo_url": None,
        }))
        zf.writestr("data/../../../escaped.txt", "no deberia terminar aca afuera")

    new_data_root = tmp_path / "data-evil"
    monkeypatch.setattr(cli_module, "DATA_ROOT", new_data_root)
    monkeypatch.setattr(cli_module, "add_project", lambda entry_obj: entry_obj)

    runner.invoke(cli_module.app, ["import", str(evil_zip)])

    assert not (tmp_path / "escaped.txt").exists()
