"""Developer tools over the index and the git history: the frames of a pasted error located in the project's code,
the history behind a piece of code (who wrote each line, in which commit and for which issue) and the commits of a
person since the last working day, for the daily stand-up."""

import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Optional

import git

from core.insight import ISSUE_REF

MAX_FRAMES = 15
_SOURCE_EXT = r"py|pyi|js|jsx|mjs|cjs|ts|tsx|go|rs|rb|php|cs|java|kt|kts|scala|c|cc|cpp|h|hpp|swift|m|dart|ex|exs|lua"
_FRAME_PATTERNS = [
    # Python: File "app/models.py", line 42, in save
    re.compile(r'File "(?P<path>[^"]+)", line (?P<line>\d+)(?:, in (?P<func>[^\s]+))?'),
    # Java / Kotlin: at com.acme.Order.total(Order.java:57)
    re.compile(r"at (?P<func>[\w$.<>]+)\((?P<path>[\w$-]+\.(?:java|kt|scala)):(?P<line>\d+)\)"),
    # JavaScript / TypeScript: at handle (src/routes/cart.ts:12:9)  or  at src/routes/cart.ts:12:9
    re.compile(r"at (?:(?P<func>[^\s(]+) \()?(?P<path>[^\s()]+?):(?P<line>\d+):\d+\)?"),
    # Anything else that looks like path:line (Go, Rust, C#, compilers and linters)
    re.compile(rf"(?P<path>[\w.@~/\\-]*[\w-]\.(?:{_SOURCE_EXT})):(?P<line>\d+)"),
]


def parse_frames(trace: str) -> list[dict]:
    """The (path, line, function) frames of a stack trace or error log, in the order they appear."""
    frames, seen = [], set()
    for raw_line in trace.splitlines():
        for pattern in _FRAME_PATTERNS:
            m = pattern.search(raw_line)
            if not m:
                continue
            path = m.group("path").replace("\\", "/")
            line = int(m.group("line"))
            func = m.groupdict().get("func")
            if (path, line) not in seen:
                seen.add((path, line))
                frames.append({"path": path, "line": line, "func": func})
            break
        if len(frames) >= MAX_FRAMES:
            break
    return frames


def error_message(trace: str) -> str:
    """The line that states the error: the last line in Python tracebacks, otherwise the first that names an error."""
    lines = [ln.strip() for ln in trace.strip().splitlines() if ln.strip()]
    if not lines:
        return ""
    if lines[0].startswith("Traceback"):
        return lines[-1][:300]
    for ln in lines:
        if re.search(r"(error|exception|panic|fatal|undefined|cannot|failed)", ln, re.IGNORECASE):
            return ln[:300]
    return lines[0][:300]


def match_repo_file(path: str, repo_files: list[str], func: Optional[str] = None) -> Optional[str]:
    """The repository file a frame points to: the longest path suffix shared with a tracked file. Java frames carry
    only the file name, so the package in the function name breaks ties."""
    parts = PurePosixPath(path).parts
    best, best_len = None, 0
    for f in repo_files:
        fparts = PurePosixPath(f).parts
        n = 0
        while n < min(len(parts), len(fparts)) and parts[-1 - n] == fparts[-1 - n]:
            n += 1
        if n > best_len or (n == best_len and n and best and func and _package_score(f, func) > _package_score(best, func)):
            best, best_len = f, n
    return best if best_len else None


def _package_score(file_path: str, func: str) -> int:
    pkg = func.split(".")[:-2]
    return sum(1 for p in pkg if f"/{p}/" in f"/{file_path}")


def locate(collection, frames: list[dict], repo_files: list[str]) -> list[dict]:
    """Each frame with the tracked file and the indexed function that contains its line (None if it is outside the
    project, for example a library)."""
    out = []
    cache: dict = {}
    for fr in frames:
        file_path = match_repo_file(fr["path"], repo_files, fr.get("func"))
        chunk = None
        if file_path:
            if file_path not in cache:
                res = collection.get(where={"file_path": file_path}, include=["metadatas", "documents"])
                cache[file_path] = list(zip(res["metadatas"], res["documents"]))
            covering = [(m, d) for m, d in cache[file_path] if m.get("chunk_type") != "overview"
                        and (m.get("start_line") or 0) <= fr["line"] <= (m.get("end_line") or 0)]
            if covering:
                m, d = min(covering, key=lambda md: md[0].get("end_line", 0) - md[0].get("start_line", 0))
                chunk = {"file_path": file_path, "chunk_type": m.get("chunk_type", "raw"), "name": m.get("name", ""),
                         "start_line": m.get("start_line", 0), "end_line": m.get("end_line", 0), "code": d}
        out.append({**fr, "file_path": file_path, "chunk": chunk})
    return out


# ---------------------------------------------------------------- history behind a piece of code
def code_history(repo_path: str, file_path: str, start: int, end: int, limit: int = 8) -> dict:
    """Who wrote each line of the range and in which commit (git blame), with the full message and the issues it
    mentions, plus the commit that created the file."""
    repo = git.Repo(repo_path)
    raw = repo.git.blame("-w", "--line-porcelain", f"-L{start},{end}", "--", file_path)
    lines_by_sha: Counter = Counter()
    for ln in raw.splitlines():
        m = re.match(r"^([0-9a-f]{40}) \d+ \d+", ln)
        if m:
            lines_by_sha[m.group(1)] += 1
    total = sum(lines_by_sha.values())
    commits = []
    for sha, n in lines_by_sha.most_common(limit):
        if set(sha) == {"0"}:
            commits.append({"sha": "sin commit", "lines": n, "author": "", "date": "", "subject": "Cambios sin confirmar en la copia local",
                            "body": "", "issues": []})
            continue
        c = repo.commit(sha)
        message = c.message.strip()
        subject, _, body = message.partition("\n")
        commits.append({"sha": sha[:7], "lines": n, "author": c.author.name, "date": c.authored_datetime.date().isoformat(),
                        "subject": subject[:200], "body": body.strip()[:800], "issues": _issue_refs(message)})
    commits.sort(key=lambda c: c["date"])
    created = None
    try:
        first = repo.git.log("--follow", "--diff-filter=A", "--format=%h\t%an\t%aI\t%s", "--", file_path).splitlines()
        if first:
            sha, author, date, subject = first[-1].split("\t", 3)
            created = {"sha": sha, "author": author, "date": date[:10], "subject": subject[:200], "issues": _issue_refs(subject)}
    except git.GitError:
        pass
    return {"file_path": file_path, "start_line": start, "end_line": end, "lines": total, "commits": commits,
            "created": created, "shallow": (Path(repo.git_dir) / "shallow").exists()}


def _issue_refs(text: str) -> list[str]:
    return list(dict.fromkeys("#" + a if a else b for a, b in ISSUE_REF.findall(text)))


# ---------------------------------------------------------------- daily stand-up
def last_working_day(now: Optional[datetime] = None) -> datetime:
    """Start of the previous working day (Friday when today is Monday)."""
    now = now or datetime.now(timezone.utc).astimezone()
    back = 3 if now.weekday() == 0 else 2 if now.weekday() == 6 else 1
    return (now - timedelta(days=back)).replace(hour=0, minute=0, second=0, microsecond=0)


def commits_since(repo_path: str, author: str, since: datetime, limit: int = 40) -> list[dict]:
    """A person's local commits since a date, with the files they changed and the issues they mention."""
    repo = git.Repo(repo_path)
    raw = repo.git.log("--no-merges", "--all", f"-n{limit}", f"--since={since.isoformat()}", f"--author={author}",
                       "--name-only", "--format=@@%h\t%aI\t%s")
    out = []
    for ln in raw.splitlines():
        if ln.startswith("@@"):
            sha, date, subject = ln[2:].split("\t", 2)
            out.append({"sha": sha, "date": date, "message": subject[:200], "files": [], "issues": _issue_refs(subject)})
        elif ln.strip() and out:
            out[-1]["files"].append(ln.strip())
    return out
