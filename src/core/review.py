"""Review before opening a pull request: what the local changes touch (functions, their callers and tests), which
changed functions have no test, which verified answers and documents they would leave pending, who should review
them, and a draft of the pull request description.

It reads the developer's working copy (committed and uncommitted changes against the base branch) and the index.
The index reflects the last sync, so line numbers of heavily edited files are an approximation.
"""

import re
from pathlib import Path
from typing import Optional

import git

from core import expertise, insight, verification
from core.engine.retrieval import TEST_PATH_PATTERN
from core.insight import ISSUE_REF

MAX_FILES = 60
MAX_TARGETS = 10
BASE_CANDIDATES = ("origin/HEAD", "origin/main", "origin/master", "main", "master", "origin/develop", "develop")
_SAFE_REF = re.compile(r"^[\w][\w./~^@{}-]{0,120}$")
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
_DEF = re.compile(r"^\s*(?:(?:export|default|async|pub(?:\([^)]*\))?|public|private|protected|internal|static|abstract|final|open|override)\s+)*"
                  r"(?:def|function\*?|func|fn|class|interface|struct|enum|trait)\s+(?:\([^)]*\)\s*)?([A-Za-z_$][\w$]*)")
_KEYWORDS = {"if", "for", "while", "switch", "catch", "return", "else", "new", "function", "super", "this"}


class InvalidBaseError(ValueError):
    pass


def _def_name(text: str) -> Optional[str]:
    """Name defined on a line with an explicit keyword (def, function, class...)."""
    m = _DEF.match(text)
    name = m.group(1) if m else None
    return None if not name or name in _KEYWORDS else name


def resolve_base(repo: git.Repo, base: Optional[str]) -> tuple[str, str]:
    """The merge-base between HEAD and the base branch (given, or the first default branch found)."""
    if base and not _SAFE_REF.match(base):
        raise InvalidBaseError(f"Rama base no válida: {base}")
    for ref in ([base] if base else BASE_CANDIDATES):
        try:
            sha = repo.git.merge_base(ref, "HEAD").strip()
        except git.GitCommandError:
            continue
        return ref, sha
    if base:
        raise InvalidBaseError(f"No encontré la rama base «{base}» en este repositorio.")
    return "HEAD", repo.head.commit.hexsha  # no base branch: only the uncommitted changes


def parse_diff(raw: str) -> list[dict]:
    """Files of a `git diff -U0` with their status, added and removed lines, changed line ranges (new side) and the
    definitions named in the changed lines."""
    files, cur = [], None
    for line in raw.splitlines():
        if line.startswith("diff --git "):
            cur = {"path": None, "old_path": None, "status": "M", "added": 0, "removed": 0, "ranges": [], "names": [], "added_text": []}
            files.append(cur)
        elif cur is None:
            continue
        elif line.startswith("--- "):
            cur["old_path"] = None if line[4:] == "/dev/null" else line[6:]
        elif line.startswith("+++ "):
            if line[4:] == "/dev/null":
                cur["status"], cur["path"] = "D", cur["old_path"]
            else:
                cur["path"] = line[6:]
                if cur["old_path"] is None:
                    cur["status"] = "A"
                elif cur["old_path"] != cur["path"]:
                    cur["status"] = "R"
        elif line.startswith("@@"):
            m = _HUNK.match(line)
            if m:
                start, count = int(m.group(1)), int(m.group(2) if m.group(2) is not None else 1)
                cur["ranges"].append((start, start + max(count, 1) - 1))
        elif line.startswith("+"):
            cur["added"] += 1
            cur["added_text"].append(line[1:])
            if name := _def_name(line[1:]):
                cur["names"].append(name)
        elif line.startswith("-"):
            cur["removed"] += 1
            if name := _def_name(line[1:]):
                cur["names"].append(name)
    return [f for f in files if f["path"]]


def local_changes(repo_path: str, base: Optional[str] = None) -> dict:
    """Committed and uncommitted changes of the working copy against the base branch, plus new untracked files."""
    repo = git.Repo(repo_path)
    ref, sha = resolve_base(repo, base)
    files = parse_diff(repo.git.diff("-U0", "--no-color", "--no-ext-diff", "--find-renames", sha))
    known = {f["path"] for f in files}
    for path in repo.git.ls_files("--others", "--exclude-standard").splitlines():
        if path in known:
            continue
        try:
            text = (Path(repo_path) / path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            text = ""
        lines = text.splitlines()
        files.append({"path": path, "old_path": None, "status": "A", "added": len(lines), "removed": 0,
                      "ranges": [(1, max(len(lines), 1))], "names": [n for n in map(_def_name, lines) if n], "added_text": lines})
    log = repo.git.log("--no-merges", "-n50", "--format=%h\t%an\t%s", f"{sha}..HEAD") if sha != repo.head.commit.hexsha else ""
    commits = []
    for line in log.splitlines():
        h, author, subject = line.split("\t", 2)
        commits.append({"sha": h, "author": author, "subject": subject, "issues": _refs(subject)})
    try:
        branch = repo.active_branch.name
    except TypeError:
        branch = ""
    try:
        me = repo.config_reader().get_value("user", "name", "")
    except Exception:  # noqa: BLE001
        me = ""
    return {"base": ref, "base_sha": sha[:10], "branch": branch, "author": str(me), "files": files[:MAX_FILES], "commits": commits,
            "diff": repo.git.diff("-U2", "--no-color", "--no-ext-diff", sha)}


def _refs(text: str) -> list:
    return list(dict.fromkeys(f"#{a}" if a else b for a, b in ISSUE_REF.findall(text)))


def changed_functions(collection, files: list[dict]) -> list[dict]:
    """Indexed functions and classes that the changes touch: by line range in the indexed version of each file and by
    the definitions named in the diff."""
    out, seen = [], set()
    for f in files:
        if f["status"] == "D":
            continue
        res = collection.get(where={"file_path": f["path"]}, include=["metadatas"])
        metas = [m for m in res["metadatas"] if m.get("chunk_type") != "overview" and m.get("name")]
        hits = [m for m in metas if any(m.get("start_line", 0) <= e and s <= m.get("end_line", 0) for s, e in f["ranges"])]
        by_name = {m["name"]: m for m in metas}
        # Prefer the innermost chunk (a method over its class).
        hits = [m for m in hits if not any(o is not m and o["start_line"] >= m["start_line"] and o["end_line"] <= m["end_line"]
                                           and (o["end_line"] - o["start_line"]) < (m["end_line"] - m["start_line"]) for o in hits)]
        for m in hits + [by_name[n] for n in f["names"] if n in by_name]:
            key = (f["path"], m["name"])
            if key not in seen:
                seen.add(key)
                out.append({"name": m["name"], "file_path": f["path"], "chunk_type": m.get("chunk_type"),
                            "start_line": m.get("start_line"), "end_line": m.get("end_line"), "new": False})
        for n in f["names"]:
            if n not in by_name and (f["path"], n) not in seen and f["status"] in ("A", "M", "R"):
                seen.add((f["path"], n))
                out.append({"name": n, "file_path": f["path"], "chunk_type": "new", "start_line": None, "end_line": None, "new": True})
    return out


def build(collection, repo_path: str, project_dir: Path, docs_dir: Path, public_key: Optional[str], base: Optional[str] = None) -> dict:
    """Everything a developer should check before opening the pull request, without the model."""
    changes = local_changes(repo_path, base)
    files = changes["files"]
    test_files = [f for f in files if TEST_PATH_PATTERN.search(f["path"])]
    code_files = [f for f in files if not TEST_PATH_PATTERN.search(f["path"])]
    functions = changed_functions(collection, code_files)
    existing = [fn for fn in functions if not fn["new"]]
    impact = insight.impact(collection, [fn["name"] for fn in existing][:MAX_TARGETS]) if existing else {"targets": [], "files": []}
    test_text = "\n".join(line for f in test_files for line in f["added_text"])
    by_target = {t["target"]: t for t in impact["targets"]}
    for fn in functions:
        t = by_target.get(fn["name"])
        fn["callers"] = len(t["callers"]) + len(t["indirect"]) if t else 0
        fn["tests"] = sorted({c["file_path"] for c in t["tests"]}) if t else []
        fn["tested_in_change"] = bool(re.search(rf"(?<![\w$]){re.escape(fn['name'])}(?![\w$])", test_text))
        fn["missing_test"] = not fn["tests"] and not fn["tested_in_change"] and fn["chunk_type"] != "class"
    tests_to_run = sorted({c["file_path"] for t in impact["targets"] for c in t["tests"]} | {f["path"] for f in test_files if f["status"] != "D"})

    changed = {f["path"] for f in files} | {f["old_path"] for f in files if f["old_path"]}
    answers = [{"id": a["id"], "question": a["question"], "reviewer": a["reviewer"], "files": sorted(set(a["files"]) & changed)}
               for a in verification._load(verification.answers_path(project_dir), []) if set(a.get("files", {})) & changed]
    docs = []
    for path in sorted(changed):
        if (docs_dir / f"{path}.md").exists():
            st = verification.doc_status(project_dir, docs_dir, path, public_key)
            if st["status"] != "draft":
                docs.append({"file_path": path, "status": st["status"], "reviewer": st.get("reviewer")})
    try:
        experts = expertise.experts(repo_path, [f["path"] for f in code_files if f["status"] != "A"][:10], top=5)["overall"]
    except (git.GitError, expertise.InvalidPathError):
        experts = []
    me = changes["author"].lower()
    reviewers = [e for e in experts if e["name"].lower() != me][:3]
    issues = list(dict.fromkeys(_refs(changes["branch"].replace("-", " ").replace("/", " ")) +
                                [r for c in changes["commits"] for r in c["issues"]]))
    if not issues and (m := re.search(r"(?:^|[/_-])(\d{1,6})(?:[/_-]|$)", changes["branch"])):
        issues = [f"#{m.group(1)}"]
    return {
        "base": changes["base"], "base_sha": changes["base_sha"], "branch": changes["branch"], "author": changes["author"],
        "files": [{k: f[k] for k in ("path", "old_path", "status", "added", "removed")} | {"test": f in test_files} for f in files],
        "commits": changes["commits"], "functions": functions, "impact_files": impact["files"], "tests_to_run": tests_to_run,
        "missing_tests": [fn for fn in functions if fn["missing_test"]], "verified_answers": answers, "verified_docs": docs,
        "reviewers": reviewers, "issues": issues, "_diff": changes["diff"],
    }
