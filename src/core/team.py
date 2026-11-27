"""Team mode: Beacon Local connected to a Beacon Hub.

The Hub signs each person's session (who they are and their Scrum role in each project) and the organization's
license. Beacon Local pins the Hub's public key and verifies both on every request, without calling the Hub, so
the code and the questions never travel to it. Without a Hub configured, Beacon works on its own (standalone mode).
"""

import logging
import os
import re
import threading
from dataclasses import dataclass
from typing import Optional, Tuple

import httpx

from core import config as _config
from hub import roles as R
from hub.security import InvalidTokenError, verify_token

logger = logging.getLogger("beacon.team")

ADMIN = "admin"
_PROJECT_PATH = re.compile(r"^/projects/([^/]+)(/.*)?$")


class LicenseExpiredError(Exception):
    pass


@dataclass
class HubSettings:
    url: str
    public_key: str


def _raw_hub() -> dict:
    try:
        return (_config._read_yaml(_config.CONFIG_PATH) or {}).get("hub") or {}
    except (FileNotFoundError, OSError):
        return {}


def hub_url() -> Optional[str]:
    url = os.environ.get("BEACON_HUB_URL") or _raw_hub().get("url")
    return url.rstrip("/") if url else None


_pin_lock = threading.Lock()


def hub_settings() -> Optional[HubSettings]:
    """The Hub this instance trusts, or None in standalone mode. The first time, the public key is fetched and
    pinned in config.yaml; after that a different key is never accepted silently.
    """
    url = hub_url()
    if not url:
        return None
    key = os.environ.get("BEACON_HUB_PUBLIC_KEY") or _raw_hub().get("public_key")
    if key:
        return HubSettings(url, key)
    with _pin_lock:
        res = httpx.get(f"{url}/public-key", timeout=5)
        res.raise_for_status()
        key = res.json()["public_key"]
        with _config._editing(_config.CONFIG_PATH, missing_ok=True) as raw:
            raw.setdefault("hub", {})["public_key"] = key
            raw["hub"].setdefault("url", url)
    return HubSettings(url, key)


def verify_session(settings: HubSettings, authorization: str) -> dict:
    return verify_token(settings.public_key, authorization.removeprefix("Bearer ").strip(), "session")


def verify_license(settings: HubSettings, license_token: str, org_id: int) -> dict:
    """Claims of the organization's license; LicenseExpiredError if it expired or belongs to another organization."""
    try:
        claims = verify_token(settings.public_key, license_token, "license")
    except InvalidTokenError as e:
        raise LicenseExpiredError("La licencia de la organización venció o no es válida. Un administrador puede "
                                  "renovarla en Equipo > Plan.") from e
    if claims.get("org") != org_id:
        raise LicenseExpiredError("La licencia no corresponde a tu organización.")
    return claims


# Routes of the Local API and what they require: (action, project_id). ADMIN means the organization administrator.
def required_permission(method: str, path: str) -> Tuple[Optional[str], Optional[str]]:
    m = _PROJECT_PATH.match(path)
    if m:
        project_id, rest = m.group(1), m.group(2) or ""
        if method == "DELETE" and rest == "":
            return ADMIN, None
        if rest == "/clean":
            return ADMIN, None
        if rest in ("/sync", "/auto-watch") and method in ("POST", "PUT"):
            return R.SYNC, project_id
        if (rest == "/docs" and method == "PUT") or rest in ("/docs/verify", "/answers/verify") or (rest.startswith("/answers/") and method == "DELETE"):
            return R.VERIFY, project_id
        return R.ASK, project_id
    if path == "/projects":
        return (ADMIN, None) if method == "POST" else (None, None)  # the list is filtered by role in the endpoint
    if path.startswith("/config") or path in ("/system/browse-dirs", "/system/available-models", "/system/storage"):
        return ADMIN, None
    return None, None  # any person of the organization (model server status, providers, monitor)


def allowed(claims: dict, action: Optional[str], project_id: Optional[str]) -> bool:
    if action is None:
        return True
    if action == ADMIN:
        return bool(claims.get("admin"))
    return R.can(claims, action, project_id)


# Events reported to the Hub's audit trail. Neither the question text nor any code is sent.
def audit_action(method: str, path: str) -> Optional[Tuple[str, Optional[str]]]:
    m = _PROJECT_PATH.match(path)
    project_id, rest = (m.group(1), m.group(2) or "") if m else (None, "")
    if method == "POST" and rest == "/query":
        return "consulta al código", project_id
    if method == "POST" and rest.startswith("/issues/") and rest.endswith("/start"):
        ref = rest.split("/")[2]
        return f"¿por dónde empiezo? (issue {'#' + ref if ref.isdigit() else ref})", project_id
    if method == "PUT" and rest == "/docs":
        return "documento corregido", project_id
    if method == "POST" and rest == "/sync":
        return "sincronización", project_id
    if method == "PUT" and rest == "/auto-watch":
        return "sincronización automática cambiada", project_id
    if method == "POST" and rest == "/clean":
        return "espacio liberado", project_id
    if method == "DELETE" and m and rest == "":
        return "proyecto quitado de Beacon Local", project_id
    if method == "POST" and path == "/projects":
        return "proyecto registrado en Beacon Local", None
    if method == "PUT" and path.startswith("/config"):
        return "servidor de modelos cambiado", None
    return None


def report(settings: HubSettings, authorization: str, action: str, project_id: Optional[str]) -> None:
    """Sends the event to the Hub in the background; if the Hub does not answer, it is logged locally."""
    def send():
        try:
            httpx.post(f"{settings.url}/audit", json={"action": action, "project_id": project_id},
                       headers={"Authorization": authorization}, timeout=5)
        except httpx.HTTPError:
            logger.warning("no se pudo registrar en el Hub: %s (%s)", action, project_id)
    threading.Thread(target=send, daemon=True).start()


def issue_question(issue: dict, language: str) -> str:
    body = (issue.get("body") or "").strip()[:1500]
    ref = issue.get("ref") or f"#{issue.get('number')}"
    if language == "en":
        return (f"I have to work on this backlog task (issue {ref}).\nTitle: {issue['title']}\n"
                f"Description: {body or '(none)'}\n\nWhich parts of the code should I review to get started, and why? "
                "Name the specific files and functions.")
    return (f"Tengo que trabajar en esta tarea del backlog (issue {ref}).\nTítulo: {issue['title']}\n"
            f"Descripción: {body or '(sin descripción)'}\n\n¿Qué partes del código tengo que revisar para empezar, y por qué? "
            "Indica los archivos y las funciones concretas.")
