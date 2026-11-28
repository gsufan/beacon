"""Files a person follows, and the commits that changed them since that person last looked.

Each followed file keeps an anchor: the last commit that had touched it when it was followed or last seen. The
changes are the commits between that anchor and HEAD, so commits that arrive later with a pull are not missed even
if they were written earlier. Following is per person (the Hub user in team mode, «local» otherwise).
"""

import json
import time
from pathlib import Path
from typing import Optional

import git

MAX_PER_PERSON = 100
MAX_CHANGES = 10


def _path(project_dir: Path) -> Path:
    return project_dir / "follows.json"


def _load(project_dir: Path) -> dict:
    try:
        return json.loads(_path(project_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(project_dir: Path, data: dict) -> None:
    path = _path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _anchor(repo: git.Repo, file_path: str) -> Optional[str]:
    try:
        return repo.git.log("-n1", "--format=%H", "--", file_path).strip() or None
    except git.GitCommandError:
        return None


def follow(project_dir: Path, repo_path: str, person: str, file_path: str, reason: str = "manual") -> dict:
    data = _load(project_dir)
    mine = data.setdefault(person, {})
    if file_path not in mine:
        if len(mine) >= MAX_PER_PERSON:
            oldest = min(mine, key=lambda f: mine[f]["ts"])
            del mine[oldest]
        mine[file_path] = {"anchor": _anchor(git.Repo(repo_path), file_path), "ts": time.time(), "reason": reason}
        _save(project_dir, data)
    return mine[file_path]


def follow_many(project_dir: Path, repo_path: str, person: str, files: list, reason: str) -> None:
    for f in dict.fromkeys(files):
        try:
            follow(project_dir, repo_path, person, f, reason)
        except (git.GitError, OSError):
            continue


def unfollow(project_dir: Path, person: str, file_path: str) -> bool:
    data = _load(project_dir)
    if file_path not in data.get(person, {}):
        return False
    del data[person][file_path]
    _save(project_dir, data)
    return True


def _changes(repo: git.Repo, anchor: Optional[str], file_path: str) -> list:
    if not anchor:
        rng = ["-n", str(MAX_CHANGES)]  # the file did not exist when followed: every commit that touched it
    else:
        try:
            repo.git.merge_base("--is-ancestor", anchor, "HEAD")
        except git.GitCommandError:
            return []  # history rewritten (rebase or another branch): nothing reliable to compare against
        rng = [f"{anchor}..HEAD", "-n", str(MAX_CHANGES)]
    try:
        raw = repo.git.log("--no-merges", *rng, "--format=%h\t%an\t%aI\t%s", "--", file_path)
    except git.GitCommandError:
        return []
    out = []
    for line in raw.splitlines():
        sha, author, date, subject = line.split("\t", 3)
        out.append({"sha": sha, "author": author, "date": date[:10], "subject": subject})
    return out


def status(project_dir: Path, repo_path: str, person: str) -> dict:
    """Followed files, each with the commits that changed it since it was last seen, changed ones first."""
    mine = _load(project_dir).get(person, {})
    repo = git.Repo(repo_path)
    files = [{"file_path": f, "since": info["ts"], "reason": info.get("reason", "manual"), "changes": _changes(repo, info.get("anchor"), f)}
             for f, info in mine.items()]
    files.sort(key=lambda x: (not x["changes"], x["file_path"]))
    return {"files": files, "changed": sum(1 for f in files if f["changes"])}


def mark_seen(project_dir: Path, repo_path: str, person: str, file_path: Optional[str] = None) -> None:
    """Moves the anchor of one followed file (or all of them) to its latest commit."""
    data = _load(project_dir)
    mine = data.get(person, {})
    repo = git.Repo(repo_path)
    for f in ([file_path] if file_path else list(mine)):
        if f in mine:
            mine[f]["anchor"] = _anchor(repo, f) or mine[f].get("anchor")
            mine[f]["ts"] = time.time()
    _save(project_dir, data)
