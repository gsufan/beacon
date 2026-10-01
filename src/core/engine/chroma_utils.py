"""Cliente ChromaDB compartido y coherencia entre procesos.

ChromaDB embebido mantiene el índice vectorial (HNSW) en memoria por proceso.
Si otro proceso escribe el mismo índice (ej. 'beacon sync' en una consola
mientras 'beacon serve' está arriba), el servidor sigue buscando en su copia
vieja: el conteo se actualiza (viene de SQLite), pero la búsqueda no encuentra
los vectores nuevos hasta reiniciar. Se reprodujo con dos procesos reales.

Solución: cada escritura del índice actualiza data/<id>/.index_version. Al
pedir un cliente, si ese marcador cambió desde la última vez que *este*
proceso lo vio, se descarta la instancia en memoria de ese proyecto (solo
esa) y ChromaDB la recarga desde disco. Las escrituras hechas por el propio
proceso registran su versión, así que no provocan recargas innecesarias.
"""

import gc
import os
import re
import shutil
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path

import chromadb
from chromadb.api.shared_system_client import SharedSystemClient
from chromadb.config import Settings

INDEX_VERSION_FILENAME = ".index_version"
_SEGMENT_DIR = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")

_seen_versions: dict = {}  # chroma_dir -> versión que este proceso ya tiene en memoria
_versions_lock = threading.Lock()


def _version_file(project_dir) -> Path:
    return Path(project_dir) / INDEX_VERSION_FILENAME


def read_index_version(project_dir) -> str:
    try:
        return _version_file(project_dir).read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""


def bump_index_version(project_dir) -> str:
    """Marca que el índice de data/<id>/ cambió. Escritura atómica (archivo
    temporal + replace) para que otro proceso nunca lea un valor a medias."""
    project_dir = Path(project_dir)
    project_dir.mkdir(parents=True, exist_ok=True)
    version = f"{time.time_ns()}-{os.getpid()}"
    tmp = project_dir / f"{INDEX_VERSION_FILENAME}.{os.getpid()}.tmp"
    tmp.write_text(version, encoding="utf-8")
    os.replace(tmp, _version_file(project_dir))
    with _versions_lock:
        _seen_versions[str(project_dir / "chroma_db")] = version
    return version


def release_chroma_client(path: str) -> None:
    """Cierra la instancia de ChromaDB de este proceso para data/<id>/chroma_db.
    En Windows no se puede borrar una carpeta con archivos abiertos, y el
    índice vectorial queda abierto mientras la instancia exista (probado: sin
    esto, borrar el proyecto fallaba con WinError 32)."""
    with _versions_lock:
        system = SharedSystemClient._identifier_to_system.pop(path, None)
        _seen_versions.pop(str(Path(path)), None)
    if system is not None:
        system.stop()
    gc.collect()  # las colecciones que aún apunten a la instancia sueltan sus archivos


def remove_orphan_segments(path: str) -> int:
    """Borra las carpetas de índices vectoriales que ya no pertenecen a
    ninguna colección. Al borrar una colección (reconstrucción del índice),
    ChromaDB en Windows no puede eliminar su carpeta porque el proceso aún la
    tiene abierta, y queda huérfana ocupando disco. Se consulta el catálogo
    de ChromaDB (tabla `segments` de chroma.sqlite3, esquema de 0.5.23) y solo
    se tocan carpetas con nombre de segmento que no figuran en él. Una que
    siga bloqueada se deja para el próximo sync. Devuelve cuántas borró."""
    try:
        with closing(sqlite3.connect(os.path.join(path, "chroma.sqlite3"))) as conn:
            live = {row[0] for row in conn.execute("SELECT id FROM segments")}
    except sqlite3.Error:
        return 0  # sin catálogo legible no se borra nada
    removed = 0
    for entry in os.listdir(path):
        full = os.path.join(path, entry)
        if os.path.isdir(full) and _SEGMENT_DIR.fullmatch(entry) and entry not in live:
            try:
                shutil.rmtree(full)
                removed += 1
            except OSError:
                pass
    return removed


def get_chroma_client(path: str) -> chromadb.PersistentClient:
    """Cliente para data/<id>/chroma_db, recargado desde disco si otro
    proceso modificó el índice desde la última vez."""
    project_dir = Path(path).parent
    with _versions_lock:
        current = read_index_version(project_dir)
        known = _seen_versions.get(str(Path(path)))
        if known is not None and known != current:
            # ChromaDB comparte una instancia por ruta dentro del proceso
            # (SharedSystemClient, API interna de chromadb==0.5.23, fijada en
            # requirements.txt; tests/test_index_freshness.py falla si cambia).
            # Quitarla fuerza a que el próximo cliente la recree desde disco.
            SharedSystemClient._identifier_to_system.pop(path, None)
        _seen_versions[str(Path(path))] = current
    # anonymized_telemetry=False: evita el error de compatibilidad
    # ChromaDB/posthog en consola (capture() takes 1 positional argument...),
    # y de paso no manda ningún dato de uso a servidores externos.
    return chromadb.PersistentClient(
        path=path,
        settings=Settings(anonymized_telemetry=False),
    )
