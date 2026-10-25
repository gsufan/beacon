"""Shared ChromaDB client and index coherence across processes.

Embedded ChromaDB keeps the vector index in memory per process: if another process modifies it (for
example `beacon sync` while `beacon serve` is running), the server does not see the new vectors until
it restarts. Every write updates data/<id>/.index_version and, when a client is requested, if the
marker changed since this process last saw it, the in-memory instance of that project is discarded and
ChromaDB reloads it from disk.
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

_seen_versions: dict = {}  # chroma_dir -> version this process already has in memory
_versions_lock = threading.Lock()


def _version_file(project_dir) -> Path:
    return Path(project_dir) / INDEX_VERSION_FILENAME


def read_index_version(project_dir) -> str:
    try:
        return _version_file(project_dir).read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""


def bump_index_version(project_dir) -> str:
    """Marks that the index in data/<id>/ changed. Atomic write (temp file + replace) so another process never reads a half-written value."""
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
    """Closes this process's ChromaDB instance for data/<id>/chroma_db. On Windows a folder with open files
    cannot be deleted, and the vector index stays open while the instance exists.
    """
    with _versions_lock:
        system = SharedSystemClient._identifier_to_system.pop(path, None)
        _seen_versions.pop(str(Path(path)), None)
    if system is not None:
        system.stop()
    gc.collect()  # collections still pointing at the instance release their files


def remove_orphan_segments(path: str) -> int:
    """Deletes vector index folders that no longer belong to any collection. When a collection is deleted
    (index rebuild), ChromaDB on Windows cannot remove its folder because the process still has it open,
    and it stays orphaned on disk. The ChromaDB catalog (`segments` table of chroma.sqlite3, 0.5.23 schema)
    is queried and only folders named like segments absent from it are touched. A folder that is still
    locked is left for the next sync. Returns how many were deleted.
    """
    try:
        with closing(sqlite3.connect(os.path.join(path, "chroma.sqlite3"))) as conn:
            live = {row[0] for row in conn.execute("SELECT id FROM segments")}
    except sqlite3.Error:
        return 0  # without a readable catalog nothing is deleted
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
    """Client for data/<id>/chroma_db, reloaded from disk if another process modified the index since the last time."""
    project_dir = Path(path).parent
    with _versions_lock:
        current = read_index_version(project_dir)
        known = _seen_versions.get(str(Path(path)))
        if known is not None and known != current:
            # ChromaDB shares one instance per path inside the process (SharedSystemClient, an internal API of
            # chromadb==0.5.23, pinned in requirements.txt; tests/test_context_and_freshness.py fails if it changes).
            # Removing it forces the next client to recreate it from disk.
            SharedSystemClient._identifier_to_system.pop(path, None)
        _seen_versions[str(Path(path))] = current
    # No telemetry: avoids a compatibility error with posthog and sends no data to external servers.
    return chromadb.PersistentClient(
        path=path,
        settings=Settings(anonymized_telemetry=False),
    )
