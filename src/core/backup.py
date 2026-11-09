"""Backup and restore of a whole installation: configuration, credentials and the index and documentation of every
project, in one .zip with a manifest.

The manifest records the Beacon version and commit, the model configuration and, per project, the embedding model,
the index format and the last indexed commit. With it a restore (or rolling back an update) keeps code, model and
index matching: if the restored Beacon uses another index format or model, the next `beacon sync` rebuilds the index
on its own. Repositories cloned by URL are not stored (they are cloned again on the next sync); the API key lives in an
environment variable and is backed up apart, in a secrets manager.

Errors raised: InvalidArchiveError and services.InvalidRequestError (restoring over an existing installation).
"""

import json
import subprocess
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from core import config as config_module
from core.config import load_config
from core.engine.chroma_utils import bump_index_version, get_chroma_client, release_chroma_client
from core.engine.index_store import IndexControl, open_code_collection
from core.engine.indexer import INDEX_FORMAT
from core.portability import InvalidArchiveError
from core.project_lock import LOCK_FILENAME, project_lock
from core.projects import DATA_ROOT
from core.services import InvalidRequestError
from core.version import VERSION

MANIFEST = "beacon-backup.json"
KIND = "beacon-backup"
_SKIPPED_DIRS = {"repo"}  # clones made by Beacon: they are cloned again from their URL


@dataclass
class BackupResult:
    path: Path
    projects: int


def _beacon_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parents[2],
                              capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def _index_info(project_dir: Path) -> dict:
    chroma_dir = project_dir / "chroma_db"
    if not chroma_dir.exists():
        return {}
    client = get_chroma_client(str(chroma_dir))
    control = IndexControl(client)
    info = {"embedding_model": control.embedding_model(open_code_collection(client)), "index_format": control.index_format(),
            "last_indexed_commit": control.last_indexed_commit(), "last_documented_commit": control.last_documented_commit()}
    del client, control
    release_chroma_client(str(chroma_dir))
    return info


def backup_installation(output: Path, *, config_path: Optional[Path] = None, credentials_path: Optional[Path] = None,
                        data_root: Optional[Path] = None) -> BackupResult:
    config_path = config_path or config_module.CONFIG_PATH
    credentials_path = credentials_path or config_module.CREDENTIALS_PATH
    data_root = data_root or DATA_ROOT
    cfg = load_config(config_path)
    manifest = {"tipo": KIND, "fecha": datetime.now(timezone.utc).isoformat(timespec="seconds"), "beacon_version": VERSION,
                "beacon_commit": _beacon_commit(), "index_format": INDEX_FORMAT, "ai_provider": cfg.ai_provider.public_dict(),
                "proyectos": []}
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(config_path, "config/config.yaml")
        if credentials_path.exists():
            zf.write(credentials_path, "config/credentials.yaml")
        for entry in cfg.projects:
            project_dir = data_root / entry.id
            if not project_dir.exists():
                continue
            with project_lock(project_dir):  # a sync running elsewhere must not change the index while it is copied
                manifest["proyectos"].append({"id": entry.id, "source_type": entry.source_type, **_index_info(project_dir)})
                for file in project_dir.rglob("*"):
                    rel = file.relative_to(project_dir)
                    if file.is_file() and file.name != LOCK_FILENAME and rel.parts[0] not in _SKIPPED_DIRS:
                        zf.write(file, (Path("data") / entry.id / rel).as_posix())
        zf.writestr(MANIFEST, json.dumps(manifest, indent=2, ensure_ascii=False))
    return BackupResult(path=output, projects=len(manifest["proyectos"]))


def read_manifest(archive: Path) -> dict:
    if not archive.exists():
        raise InvalidArchiveError(f"No existe el archivo '{archive}'.")
    with zipfile.ZipFile(archive) as zf:
        try:
            manifest = json.loads(zf.read(MANIFEST))
        except KeyError:
            raise InvalidArchiveError(f"El .zip no tiene {MANIFEST}: ¿fue generado con 'beacon backup'?") from None
    if manifest.get("tipo") != KIND:
        raise InvalidArchiveError("El .zip no es un respaldo de Beacon.")
    return manifest


def restore_installation(archive: Path, *, force: bool = False, config_path: Optional[Path] = None,
                         credentials_path: Optional[Path] = None, data_root: Optional[Path] = None) -> dict:
    """Restores configuration, credentials and project data. Without `force` it refuses to overwrite an installation."""
    config_path = config_path or config_module.CONFIG_PATH
    credentials_path = credentials_path or config_module.CREDENTIALS_PATH
    data_root = (data_root or DATA_ROOT).resolve()
    manifest = read_manifest(archive)
    if config_path.exists() and not force:
        raise InvalidRequestError(f"Ya hay una configuración en {config_path}; usa --force para reemplazarla con el respaldo.")
    targets = {"config/config.yaml": config_path, "config/credentials.yaml": credentials_path}
    with zipfile.ZipFile(archive) as zf:
        for member in zf.namelist():
            if member in targets:
                dest = targets[member]
            elif member.startswith("data/"):
                dest = (data_root / Path(member).relative_to("data")).resolve()
                if not dest.is_relative_to(data_root):
                    continue  # a tampered .zip must not write outside data/
            else:
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as src, open(dest, "wb") as out:
                out.write(src.read())
    for project in manifest["proyectos"]:
        bump_index_version(data_root / project["id"])  # a running server must reload these indexes
    return manifest
