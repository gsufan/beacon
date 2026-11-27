"""This Beacon Local as an installation registered with Beacon Hub.

Every few minutes it sends a heartbeat and receives a signed status: the leased license, or the order to wipe. If
the installation is revoked (or its owner left the organization), everything Beacon keeps is deleted: indexes,
documentation, URL clones, registered projects and tokens. Folders that belong to the person (a local repository
path) are never touched. Without contact, the lease lasts a few days and then Beacon stops serving.
"""

import json
import logging
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Optional

import httpx

from core import config as _config
from core import edition
from core.projects import DATA_ROOT
from hub.security import InvalidTokenError, verify_token

logger = logging.getLogger("beacon.instance")
HEARTBEAT_SECONDS = float(os.environ.get("BEACON_HEARTBEAT_SECONDS", "120"))
STATE_FILE = DATA_ROOT / ".instance_state.json"


class InstanceBlockedError(Exception):
    """This installation may not serve: revoked, lease expired, or a commercial build that is not registered."""


def credentials() -> Optional[dict]:
    hub = (_config._read_yaml(_config.CONFIG_PATH) or {}).get("hub") or {} if _config.CONFIG_PATH.exists() else {}
    if hub.get("instance_id") and hub.get("instance_secret"):
        return {"id": hub["instance_id"], "secret": hub["instance_secret"]}
    return None


def save_credentials(instance_id: str, secret: str, hub_url: str) -> None:
    with _config._editing(_config.CONFIG_PATH, missing_ok=True) as raw:
        h = raw.setdefault("hub", {})
        h.update({"instance_id": instance_id, "instance_secret": secret})
        h.setdefault("url", hub_url)


def read_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state), encoding="utf-8")


def wipe() -> list:
    """Deletes everything Beacon keeps for the organization. Returns the removed project ids."""
    from core import services  # local import: services imports the engine
    removed = []
    try:
        projects = list((_config._read_yaml(_config.CONFIG_PATH) or {}).get("projects", []))
    except OSError:
        projects = []
    for p in projects:
        try:
            services.unregister_project(p["id"], purge_data=True)
            removed.append(p["id"])
        except Exception:  # noqa: BLE001 - keep wiping the rest; leftovers are removed below
            logger.exception("borrado: no se pudo quitar el proyecto %s", p.get("id"))
    for child in DATA_ROOT.iterdir() if DATA_ROOT.exists() else []:
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
    try:
        _config.CREDENTIALS_PATH.unlink(missing_ok=True)
    except OSError:
        logger.warning("borrado: no se pudo eliminar credentials.yaml")
    return removed


def apply_status(status_token: str, license_token: Optional[str], public_key: str) -> dict:
    """Verifies the Hub's signed answer, wipes if ordered, and stores the lease."""
    claims = verify_token(public_key, status_token, "instance")
    state = read_state()
    if claims["revoked"]:
        if not state.get("wiped"):
            removed = wipe()
            logger.warning("instalación revocada por Beacon Hub: datos borrados (%d proyectos)", len(removed))
        state = {"revoked": True, "wiped": True, "checked": time.time()}
    else:
        state = {"revoked": False, "lease_exp": claims["exp"], "license": license_token, "checked": time.time()}
    _write_state(state)
    return state


def heartbeat(hub_url: str, public_key: str, version: str = "") -> Optional[dict]:
    creds = credentials()
    if not creds:
        return None
    res = httpx.post(f"{hub_url}/instances/heartbeat", json={"instance_id": creds["id"], "secret": creds["secret"], "version": version},
                     timeout=10)
    res.raise_for_status()
    body = res.json()
    return apply_status(body["status"], body.get("license"), public_key)


def check() -> None:
    """Raises InstanceBlockedError if this installation may not serve right now."""
    state = read_state()
    if state.get("revoked"):
        raise InstanceBlockedError("Esta instalación fue revocada por la organización y sus datos se borraron.")
    registered = credentials() is not None
    if registered and state.get("lease_exp") and state["lease_exp"] < time.time():
        raise InstanceBlockedError("La licencia de esta instalación venció: no pudo renovarse con Beacon Hub en los últimos días.")
    if edition.is_commercial() and not registered:
        raise InstanceBlockedError("Esta edición de Beacon debe registrarse en Beacon Hub: un administrador puede hacerlo al iniciar sesión.")


def run(stop: threading.Event, settings_fn, version: str) -> None:
    """Heartbeat loop, started by the server while a Hub is configured."""
    while True:
        try:
            settings = settings_fn()
            if settings is not None:
                heartbeat(settings.url, settings.public_key, version)
        except (httpx.HTTPError, InvalidTokenError, KeyError, ValueError) as e:
            logger.warning("latido con Beacon Hub no completado: %s", e)
        if stop.wait(HEARTBEAT_SECONDS):
            return
