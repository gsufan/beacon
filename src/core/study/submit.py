"""Optional submission of the study files to a collector, always started by the participant.

The collector is a small web endpoint that stores what it receives (see `tools/study_collector.gs`). Its address
comes from `BEACON_STUDY_SUBMIT_URL` or, if unset, from `DEFAULT_SUBMIT_URL`. With no address the wizard offers only
the manual route (download or copy the file). The payload is exactly the reviewed results file, plus the identity file
only if the participant chooses to include it.
"""

import json
import os
from typing import Optional
from urllib.parse import urlparse

import httpx

DEFAULT_SUBMIT_URL = ""
MAX_PAYLOAD_BYTES = 2_000_000
TIMEOUT_SECONDS = 30


class SubmitError(RuntimeError):
    pass


def submit_url() -> str:
    url = (os.environ.get("BEACON_STUDY_SUBMIT_URL") or DEFAULT_SUBMIT_URL).strip()
    parsed = urlparse(url)
    local = parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost")  # to try a collector on this machine
    return url if parsed.scheme == "https" or local else ""


def destination_host() -> str:
    return urlparse(submit_url()).hostname or ""


def build_payload(result: dict, identity: Optional[dict]) -> bytes:
    payload = json.dumps({"resultado": result, "identidad": identity}, ensure_ascii=False).encode("utf-8")
    if len(payload) > MAX_PAYLOAD_BYTES:
        raise SubmitError("El archivo es demasiado grande para enviarlo (más de 2 MB). Descárgalo y envíalo por el canal de la invitación.")
    return payload


def send(payload: bytes, url: Optional[str] = None) -> str:
    """Posts the payload and returns the receipt id the collector answers with. Raises SubmitError with a message for the participant."""
    url = url or submit_url()
    if not url:
        raise SubmitError("Este estudio no tiene un destino de envío configurado: descarga el archivo y envíalo por el canal de la invitación.")
    try:
        # text/plain avoids a CORS preflight in the collector (Apps Script) and keeps the body as sent.
        response = httpx.post(url, content=payload, headers={"Content-Type": "text/plain;charset=utf-8"},
                              timeout=TIMEOUT_SECONDS, follow_redirects=True)
    except httpx.TimeoutException:
        raise SubmitError("El servidor de recepción no respondió a tiempo. Vuelve a intentarlo o descarga el archivo y envíalo a mano.")
    except httpx.HTTPError:
        raise SubmitError("No se pudo conectar con el servidor de recepción. Revisa tu conexión y vuelve a intentarlo, o descarga el archivo y envíalo a mano.")
    try:
        data = response.json()
    except ValueError:
        data = None
    if response.status_code != 200 or not isinstance(data, dict) or not data.get("ok"):
        detail = data.get("error") if isinstance(data, dict) else None
        raise SubmitError(f"El servidor de recepción rechazó el envío{': ' + str(detail) if detail else ''}. Descarga el archivo y envíalo a mano.")
    return str(data.get("recibo") or "")
