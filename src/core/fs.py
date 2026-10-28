"""File-system helpers."""

import os
import shutil
import stat
from pathlib import Path


def _make_writable_and_retry(func, path, _exc_info):
    """Git marks its object files read-only; on Windows that makes a plain rmtree fail with 'access denied'."""
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        raise


def remove_tree(path, ignore_errors: bool = False):
    """Deletes a directory tree, including read-only files (a cloned repository's .git).

    With `ignore_errors`, whatever cannot be deleted (for example a file another process has open) is left behind.
    """
    if not Path(path).exists():
        return
    try:
        shutil.rmtree(path, onerror=_make_writable_and_retry)
    except OSError:
        if not ignore_errors:
            raise
