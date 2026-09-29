"""Bloqueo por proyecto que funciona entre procesos distintos.

El servidor (`beacon serve`, con su watcher automático) y la CLI (`beacon
sync`, `beacon docs`...) son procesos separados que escriben sobre el mismo
índice de ChromaDB y la misma carpeta data/<id>/. El `_claim_sync` de la API
solo coordina dentro del proceso del servidor; este lock coordina a todos.

Se usa un bloqueo del sistema operativo sobre data/<id>/.sync.lock
(`msvcrt.locking` en Windows, `fcntl.flock` en Linux/macOS). A diferencia de
un archivo "bandera", el sistema operativo lo libera solo si el proceso muere
(Ctrl+C, cierre de la consola, caída), así que nunca queda un lock huérfano
que haya que borrar a mano.
"""

import os
from contextlib import contextmanager
from pathlib import Path

LOCK_FILENAME = ".sync.lock"


class ProjectBusyError(RuntimeError):
    """Otro proceso (u otra operación) ya está modificando este proyecto."""


if os.name == "nt":
    import msvcrt

    def _try_lock(fd: int) -> bool:
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)  # msvcrt desbloquea desde la posición actual
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
    """Toma el lock exclusivo de data/<id>/ sin esperar: si otro proceso lo
    tiene, lanza ProjectBusyError de inmediato en vez de quedarse colgado."""
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
    """Falla con ProjectBusyError si alguien tiene el lock ahora mismo; si no,
    no deja nada tomado. Para operaciones cortas como borrar la carpeta."""
    if not Path(project_dir).exists():
        return
    with project_lock(project_dir):
        pass
