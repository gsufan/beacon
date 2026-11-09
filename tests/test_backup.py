"""A full backup restores configuration, credentials and project data in another installation, keeping the
manifest that ties code, model and index together."""

import json
import zipfile

import pytest
import yaml

from core import backup
from core.portability import InvalidArchiveError
from core.services import InvalidRequestError


def _installation(root):
    config = root / "config" / "config.yaml"
    credentials = root / "config" / "credentials.yaml"
    data = root / "data"
    config.parent.mkdir(parents=True)
    config.write_text(yaml.safe_dump({
        "ai_provider": {"provider": "ollama", "host": "http://localhost:11434", "embedding_model": "qwen3-embedding:0.6b", "llm_model": "llama3:8b"},
        "projects": [{"id": "demo", "name": "Demo", "repo_path": str(root / "repo"), "source_type": "git", "repo_url": "https://example.com/r.git"}],
    }), encoding="utf-8")
    credentials.write_text(yaml.safe_dump({"projects": {"demo": "token"}}), encoding="utf-8")
    (data / "demo" / "docs").mkdir(parents=True)
    (data / "demo" / "docs" / "a.py.md").write_text("# a", encoding="utf-8")
    (data / "demo" / "repo").mkdir()
    (data / "demo" / "repo" / "big.bin").write_bytes(b"x" * 100)
    return config, credentials, data


def test_backup_and_restore_round_trip(tmp_path):
    config, credentials, data = _installation(tmp_path / "a")
    archive = tmp_path / "respaldo.zip"
    result = backup.backup_installation(archive, config_path=config, credentials_path=credentials, data_root=data)
    assert result.projects == 1
    names = zipfile.ZipFile(archive).namelist()
    assert "config/credentials.yaml" in names and "data/demo/docs/a.py.md" in names
    assert not any("/repo/" in n for n in names)  # clones are not stored
    manifest = json.loads(zipfile.ZipFile(archive).read(backup.MANIFEST))
    assert manifest["ai_provider"]["embedding_model"] == "qwen3-embedding:0.6b" and manifest["index_format"]

    target = tmp_path / "b"
    new_config, new_credentials, new_data = target / "config" / "config.yaml", target / "config" / "credentials.yaml", target / "data"
    backup.restore_installation(archive, config_path=new_config, credentials_path=new_credentials, data_root=new_data)
    assert new_config.read_text(encoding="utf-8") == config.read_text(encoding="utf-8")
    assert yaml.safe_load(new_credentials.read_text(encoding="utf-8"))["projects"]["demo"] == "token"
    assert (new_data / "demo" / "docs" / "a.py.md").read_text(encoding="utf-8") == "# a"


def test_restore_refuses_to_overwrite_without_force(tmp_path):
    config, credentials, data = _installation(tmp_path / "a")
    archive = tmp_path / "respaldo.zip"
    backup.backup_installation(archive, config_path=config, credentials_path=credentials, data_root=data)
    with pytest.raises(InvalidRequestError):
        backup.restore_installation(archive, config_path=config, credentials_path=credentials, data_root=data)
    backup.restore_installation(archive, force=True, config_path=config, credentials_path=credentials, data_root=data)


def test_restore_ignores_paths_outside_data_and_rejects_other_zips(tmp_path):
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr(backup.MANIFEST, json.dumps({"tipo": backup.KIND, "proyectos": []}))
        zf.writestr("data/../../escapado.txt", "x")
    backup.restore_installation(evil, config_path=tmp_path / "c" / "config.yaml", credentials_path=tmp_path / "c" / "cred.yaml",
                                data_root=tmp_path / "d")
    assert not (tmp_path / "escapado.txt").exists()
    other = tmp_path / "other.zip"
    with zipfile.ZipFile(other, "w") as zf:
        zf.writestr("manifest.json", "{}")
    with pytest.raises(InvalidArchiveError):
        backup.read_manifest(other)
