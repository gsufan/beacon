"""Jira Cloud client: issues of a project and comments on them (REST API v3, email + API token)."""

import re
from typing import Optional

import httpx

KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,19}$")
ISSUE_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,19}-\d+$")
_FIELDS = "summary,description,assignee,labels,updated,status,comment"


class JiraError(RuntimeError):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


def normalize_site(url: str) -> str:
    url = url.strip().rstrip("/")
    if not re.match(r"^https://[A-Za-z0-9.-]+\.atlassian\.net$", url):
        raise ValueError("La dirección de Jira debe tener la forma https://<sitio>.atlassian.net.")
    return url


def normalize_project(key: str) -> str:
    key = key.strip().upper()
    if not KEY_RE.match(key):
        raise ValueError("La clave del proyecto de Jira debe ser como «BEAC» (letras mayúsculas y números).")
    return key


def adf_text(node) -> str:
    """Plain text of an Atlassian Document Format value (descriptions and comments)."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(adf_text(n) for n in node)
    if node.get("type") == "text":
        return node.get("text", "")
    inner = adf_text(node.get("content", []))
    return inner + ("\n" if node.get("type") in ("paragraph", "heading", "listItem", "codeBlock") else "")


def adf_doc(text: str) -> dict:
    return {"type": "doc", "version": 1, "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": line}]} if line else {"type": "paragraph", "content": []}
        for line in text.split("\n")
    ]}


class JiraClient:
    def __init__(self, site: str, email: str, token: str, transport: Optional[httpx.BaseTransport] = None):
        self.site = site
        self._http = httpx.Client(base_url=f"{site}/rest/api/3", auth=(email, token), timeout=15, transport=transport,
                                  headers={"Accept": "application/json", "User-Agent": "beacon-hub"})

    def _call(self, method: str, path: str, **kw):
        try:
            res = self._http.request(method, path, **kw)
        except httpx.HTTPError as e:
            raise JiraError(f"No se pudo conectar con Jira: {e}") from e
        if res.status_code in (401, 403):
            raise JiraError("Jira rechazó el acceso: revisa el correo y el token de la organización.")
        if res.status_code == 404:
            raise JiraError("Jira no encontró el proyecto o la issue.", 404)
        if res.status_code >= 400:
            raise JiraError(f"Jira respondió {res.status_code}.")
        return res.json() if res.content else {}

    def _issue(self, raw: dict) -> dict:
        f = raw.get("fields", {})
        assignee = f.get("assignee") or {}
        return {
            "id": raw["key"], "ref": raw["key"], "source": "jira",
            "title": f.get("summary") or "", "body": adf_text(f.get("description")).strip()[:8000],
            "state": (f.get("status") or {}).get("name", ""), "url": f"{self.site}/browse/{raw['key']}",
            "assignees": [assignee["displayName"]] if assignee.get("displayName") else [],
            "labels": f.get("labels") or [], "updated_at": f.get("updated"),
            "comments": ((f.get("comment") or {}).get("total")) or 0,
        }

    def issues(self, project: str, assignee: Optional[str] = None, limit: int = 50, done: bool = False) -> list[dict]:
        jql = f'project = "{project}" AND statusCategory {"=" if done else "!="} Done'
        if assignee:
            safe = assignee.replace('"', "")
            jql += f' AND assignee = "{safe}"'
        data = self._call("GET", "/search/jql", params={"jql": jql + " ORDER BY updated DESC", "fields": _FIELDS, "maxResults": min(limit, 100)})
        return [self._issue(i) for i in data.get("issues", [])]

    def issue(self, key: str) -> dict:
        if not ISSUE_KEY_RE.match(key):
            raise JiraError("Clave de issue de Jira no válida.", 400)
        return self._issue(self._call("GET", f"/issue/{key}", params={"fields": _FIELDS}))

    def comment(self, key: str, text: str) -> str:
        if not ISSUE_KEY_RE.match(key):
            raise JiraError("Clave de issue de Jira no válida.", 400)
        data = self._call("POST", f"/issue/{key}/comment", json={"body": adf_doc(text)})
        return f"{self.site}/browse/{key}?focusedCommentId={data.get('id', '')}"
