"""Concurrent writes to config.yaml and credentials.yaml must not lose changes or leave temporary files."""

import threading

import yaml

from core.config import (
    ProjectEntry, add_project, default_ai_provider, delete_project_token, get_project_token, load_config,
    remove_project, set_auto_watch, set_project_token,
)


def _config(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"ai_provider": {"provider": "ollama"}, "projects": []}), encoding="utf-8")
    return path


def test_concurrent_adds_are_all_kept(tmp_path):
    path = _config(tmp_path)
    threads = [threading.Thread(target=add_project, args=(ProjectEntry(id=f"p{i}", name=f"p{i}", repo_path="."), path))
               for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(p.id for p in load_config(path).projects) == sorted(f"p{i}" for i in range(20))
    assert not list(tmp_path.glob("*.tmp"))


def test_unchanged_file_is_not_rewritten(tmp_path):
    path = _config(tmp_path)
    before = path.stat().st_mtime_ns
    assert not remove_project("missing", path)
    assert not set_auto_watch("missing", True, path)
    assert path.stat().st_mtime_ns == before


def test_failed_change_leaves_file_intact(tmp_path):
    path = _config(tmp_path)
    add_project(ProjectEntry(id="a", name="a", repo_path="."), path)
    original = path.read_text(encoding="utf-8")
    try:
        add_project(ProjectEntry(id="a", name="dup", repo_path="."), path)
    except Exception:  # noqa: BLE001
        pass
    assert path.read_text(encoding="utf-8") == original


def test_find_project(tmp_path):
    path = _config(tmp_path)
    add_project(ProjectEntry(id="a", name="A", repo_path="."), path)
    cfg = load_config(path)
    assert cfg.find_project("a").name == "A"
    assert cfg.find_project("b") is None


def test_tokens_roundtrip(tmp_path):
    path = tmp_path / "credentials.yaml"
    set_project_token("a", "secret", path)
    assert get_project_token("a", path) == "secret"
    delete_project_token("a", path)
    assert get_project_token("a", path) is None


def test_default_ai_provider():
    ai = default_ai_provider()
    assert (ai.provider, ai.llm_model, ai.embedding_model) == ("ollama", "llama3:8b", "qwen3-embedding:0.6b")
    assert ai.host.startswith("http://localhost")
