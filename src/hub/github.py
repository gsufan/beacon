"""Read-only GitHub client: issues and recent commits of a repository (metadata, not code)."""

import re
from typing import Optional

import httpx

API = "https://api.github.com"
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_ISSUE_REF = re.compile(r"(?<![\w/])#(\d+)\b")


class GitHubError(RuntimeError):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


def normalize_repo(value: str) -> str:
    """'owner/name' from 'owner/name' or a github.com URL."""
    value = value.strip().removesuffix(".git").rstrip("/")
    value = re.sub(r"^(https?://)?(www\.)?github\.com/", "", value)
    if not REPO_RE.match(value):
        raise ValueError("El repositorio de GitHub debe tener la forma «dueño/nombre».")
    return value


def issue_refs(message: str) -> list[int]:
    """Issue numbers a commit message mentions (#123)."""
    return sorted({int(n) for n in _ISSUE_REF.findall(message)})


class GitHubClient:
    def __init__(self, token: Optional[str], transport: Optional[httpx.BaseTransport] = None):
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "beacon-hub"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._http = httpx.Client(base_url=API, headers=headers, timeout=15, transport=transport)

    def _get(self, path: str, params: Optional[dict] = None):
        try:
            res = self._http.get(path, params=params)
        except httpx.HTTPError as e:
            raise GitHubError(f"No se pudo conectar con GitHub: {e}") from e
        if res.status_code == 404:
            raise GitHubError("GitHub no encontró el repositorio o el recurso (¿el token tiene acceso?).", 404)
        if res.status_code in (401, 403):
            raise GitHubError("GitHub rechazó el acceso: revisa el token de la organización o el límite de consultas.", 502)
        if res.status_code >= 400:
            raise GitHubError(f"GitHub respondió {res.status_code}.")
        return res.json()

    @staticmethod
    def _issue(raw: dict) -> dict:
        return {
            "number": raw["number"],
            "title": raw["title"],
            "body": (raw.get("body") or "")[:8000],
            "state": raw["state"],
            "url": raw["html_url"],
            "author": (raw.get("user") or {}).get("login"),
            "assignees": [a["login"] for a in raw.get("assignees") or []],
            "labels": [lb["name"] for lb in raw.get("labels") or []],
            "updated_at": raw.get("updated_at"),
            "comments": raw.get("comments", 0),
        }

    def issues(self, repo: str, assignee: Optional[str] = None, state: str = "open", limit: int = 50) -> list[dict]:
        params = {"state": state, "per_page": min(limit, 100), "sort": "updated"}
        if assignee:
            params["assignee"] = assignee
        raw = self._get(f"/repos/{repo}/issues", params)
        return [self._issue(i) for i in raw if "pull_request" not in i][:limit]  # this endpoint also returns PRs

    def issue(self, repo: str, number: int) -> dict:
        return self._issue(self._get(f"/repos/{repo}/issues/{number}"))

    def commits(self, repo: str, limit: int = 30, author: Optional[str] = None) -> list[dict]:
        params = {"per_page": min(limit, 100)}
        if author:
            params["author"] = author
        out = []
        for c in self._get(f"/repos/{repo}/commits", params)[:limit]:
            message = c["commit"]["message"]
            out.append({
                "sha": c["sha"][:7],
                "message": message.split("\n", 1)[0][:200],
                "author": (c.get("author") or {}).get("login") or c["commit"]["author"]["name"],
                "date": c["commit"]["author"]["date"],
                "url": c["html_url"],
                "issues": issue_refs(message),
            })
        return out
