"""Slack through an incoming webhook: the organization picks the channel when it creates the webhook."""

import logging
import re
import threading
from typing import Optional

import httpx

logger = logging.getLogger("beacon.hub.slack")
WEBHOOK_RE = re.compile(r"^https://hooks\.slack\.com/services/[A-Za-z0-9/_-]+$")


class SlackError(RuntimeError):
    pass


def normalize_webhook(url: str) -> str:
    url = url.strip()
    if not WEBHOOK_RE.match(url):
        # Only Slack's own address is accepted, so the Hub cannot be used to send requests to other hosts.
        raise ValueError("El webhook de Slack debe empezar con https://hooks.slack.com/services/.")
    return url


class SlackClient:
    def __init__(self, webhook: str, transport: Optional[httpx.BaseTransport] = None):
        self.webhook = webhook
        self._transport = transport

    def send(self, text: str) -> None:
        try:
            with httpx.Client(timeout=10, transport=self._transport) as c:
                res = c.post(self.webhook, json={"text": text[:3500]})
        except httpx.HTTPError as e:
            raise SlackError(f"No se pudo conectar con Slack: {e}") from e
        if res.status_code >= 400:
            raise SlackError(f"Slack rechazó el mensaje ({res.status_code}): revisa el webhook de la organización.")

    def send_later(self, text: str) -> None:
        """Team notifications: they must not delay nor break the action that triggered them."""
        def run():
            try:
                self.send(text)
            except SlackError as e:
                logger.warning("aviso a Slack no enviado: %s", e)
        threading.Thread(target=run, daemon=True).start()
