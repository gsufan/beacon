"""Per-project lock across processes.

The server (with its watcher) and the CLI are separate processes that write to the same index and the
same data/<id>/ folder. An operating system lock on data/<id>/.sync.lock is used (`msvcrt.locking` on
Windows, `fcntl.flock` on Linux and macOS); the system releases it if the process dies, so a stale lock
is never left behind.
"""

import os
from contextlib import contextmanager
from pathlib import Path

LOCK_FILENAME = ".sync.lock"


class ProjectBusyError(RuntimeError):
    pass


if os.name == "nt":
    import msvcrt

    def _try_lock(fd: int) -> bool:
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)  # msvcrt unlocks from the current position
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _try_lock(fd: int) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


@contextmanager
def project_lock(project_dir):
    """Takes the exclusive lock of data/<id>/ without waiting: if another process holds it, it raises ProjectBusyError immediately."""
    lock_dir = Path(project_dir)
    lock_dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_dir / LOCK_FILENAME, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        if not _try_lock(fd):
            raise ProjectBusyError(
                f"El proyecto '{lock_dir.name}' se está sincronizando en otro proceso "
                "(el servidor, su watcher automático u otra consola). Espera a que termine."
            )
        try:
            yield
        finally:
            _unlock(fd)
    finally:
        os.close(fd)


def ensure_not_busy(project_dir) -> None:
    """Raises ProjectBusyError if someone holds the lock right now; otherwise it leaves nothing taken. For short operations such as deleting the folder."""
    if not Path(project_dir).exists():
        return
    with project_lock(project_dir):
        pass
