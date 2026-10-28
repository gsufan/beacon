"""Deleting directory trees that contain read-only files (a cloned repository's .git on Windows)."""
import os
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.fs import remove_tree  # noqa: E402


def _tree_with_readonly_file(root: Path) -> Path:
    objects = root / "repo" / ".git" / "objects" / "pack"
    objects.mkdir(parents=True)
    pack = objects / "pack-1.idx"
    pack.write_bytes(b"x")
    os.chmod(pack, stat.S_IREAD)
    return root


def test_remove_tree_deletes_read_only_files(tmp_path):
    remove_tree(_tree_with_readonly_file(tmp_path / "a"))
    assert not (tmp_path / "a").exists()


def test_remove_tree_ignoring_errors_also_deletes_read_only_files(tmp_path):
    remove_tree(_tree_with_readonly_file(tmp_path / "b"), ignore_errors=True)
    assert not (tmp_path / "b").exists()


def test_remove_tree_of_a_missing_folder_is_a_no_op(tmp_path):
    remove_tree(tmp_path / "nada")
    remove_tree(tmp_path / "nada", ignore_errors=True)
