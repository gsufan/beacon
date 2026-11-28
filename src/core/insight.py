"""Team insights computed from what Beacon Local already has: the index (who calls what), the git history (who knows
what, which commits mention an issue) and an anonymous usage log per module (where people ask most and where Beacon
could not answer). Nothing here leaves the organization.
"""

import json
import re
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Optional

import git

from core.engine.retrieval import TEST_PATH_PATTERN

MAX_CALLERS = 25
NO_INFO = re.compile(r"no (hay|tengo|encontr[eé]|se encontr[oó]) (información|informacion) suficiente|no (aparece|está) en el (código|contexto)|"
                     r"not enough information|no information", re.IGNORECASE)
ISSUE_REF = re.compile(r"(?<![\w/])#(\d+)\b|\b([A-Z][A-Z0-9_]{1,19}-\d+)\b")


def _module(path: str, depth: int = 2) -> str:
    parts = PurePosixPath(path).parts
    return "/".join(parts[:depth]) if len(parts) > depth else ("/".join(parts[:-1]) or ".")


# ---------------------------------------------------------------- impact radius
def _callers(collection, name: str, exclude_ids: set) -> list:
    res = collection.get(where_document={"$contains": f"{name}("}, include=["metadatas", "documents"])
    out = []
    for cid, meta, doc in zip(res["ids"], res["metadatas"], res["documents"]):
        if cid in exclude_ids or meta.get("name") == name:
            continue
        if re.search(rf"(?<![A-Za-z0-9_]){re.escape(name)}\s*\(", doc):
            out.append({"id": cid, "name": meta.get("name") or "(módulo)", "file_path": meta.get("file_path"),
                        "start_line": meta.get("start_line"), "end_line": meta.get("end_line"), "chunk_type": meta.get("chunk_type"),
                        "test": bool(TEST_PATH_PATTERN.search(meta.get("file_path", "")))})
    return out[:MAX_CALLERS]


def impact(collection, targets: list[str]) -> dict:
    """What depends on each target function: direct callers, callers of those callers (second level), and the tests
    that exercise them. `targets` are function or method names."""
    result = []
    for name in dict.fromkeys(t.strip() for t in targets if t.strip()):
        defs = collection.get(where={"name": name}, include=["metadatas"])
        def_ids = set(defs["ids"])
        definitions = [{"file_path": m.get("file_path"), "start_line": m.get("start_line"), "end_line": m.get("end_line")}
                       for m in defs["metadatas"]]
        level1 = _callers(collection, name, def_ids)
        seen = def_ids | {c["id"] for c in level1}
        level2 = []
        for c in [c for c in level1 if not c["test"] and c["chunk_type"] in ("function", "method")][:8]:
            for cc in _callers(collection, c["name"], seen):
                seen.add(cc["id"])
                cc["via"] = c["name"]
                level2.append(cc)
        tests = [c for c in level1 + level2 if c["test"]]
        result.append({"target": name, "definitions": definitions,
                       "callers": [c for c in level1 if not c["test"]], "indirect": [c for c in level2 if not c["test"]][:MAX_CALLERS],
                       "tests": tests, "untested": not tests})
    files = sorted({d["file_path"] for r in result for d in r["definitions"]} |
                   {c["file_path"] for r in result for c in r["callers"] + r["indirect"]})
    return {"targets": result, "files": files}


# ---------------------------------------------------------------- knowledge map and bus factor
def bus_factor(counts: Counter) -> int:
    """Fewest people who together account for more than half of the commits (a common approximation of the
    truck factor)."""
    total, acc = sum(counts.values()), 0
    for i, (_, c) in enumerate(counts.most_common(), 1):
        acc += c
        if acc * 2 > total:
            return i
    return 0


def knowledge_map(repo_path: str, depth: int = 2, max_commits: int = 3000) -> dict:
    repo = git.Repo(repo_path)
    raw = repo.git.log("--no-merges", f"-n{max_commits}", "--name-only", "--format=@@%an\t%aI")
    per_module = defaultdict(Counter)
    last = {}
    files_per_module = defaultdict(set)
    author = date = None
    for line in raw.splitlines():
        if line.startswith("@@"):
            author, date = line[2:].split("\t", 1)
        elif line.strip() and author:
            mod = _module(line.strip(), depth)
            per_module[mod][author] += 1
            files_per_module[mod].add(line.strip())
            last[mod] = max(last.get(mod, ""), date)
    tracked = set(repo.git.ls_files().splitlines())
    modules = []
    for mod, counts in per_module.items():
        files = {f for f in files_per_module[mod] if f in tracked}
        if not files:
            continue
        bf = bus_factor(counts)
        modules.append({"module": mod, "files": len(files), "commits": sum(counts.values()), "people": len(counts),
                        "bus_factor": bf, "risk": "alto" if bf <= 1 else "medio" if bf == 2 else "bajo",
                        "tests": sum(1 for f in files if TEST_PATH_PATTERN.search(f)),
                        "experts": [{"name": n, "commits": c} for n, c in counts.most_common(3)], "last": last.get(mod)})
    modules.sort(key=lambda m: (m["bus_factor"], -m["commits"]))
    all_counts = sum(per_module.values(), Counter())
    return {"modules": modules, "project_bus_factor": bus_factor(all_counts), "people": len(all_counts)}


# ---------------------------------------------------------------- code linked to an issue
def issue_code(repo_path: str, ref: str, limit: int = 30) -> dict:
    """Commits whose message mentions the issue (#123 or KEY-123) and the files they changed."""
    # POSIX ERE without \b: macOS git does not support it.
    pattern = f"#{ref}([^0-9]|$)" if ref.isdigit() else re.escape(ref)
    repo = git.Repo(repo_path)
    raw = repo.git.log("--no-merges", "-E", f"--grep={pattern}", f"-n{limit}", "--name-only", "--format=@@%h\t%an\t%aI\t%s")
    commits, files = [], Counter()
    for line in raw.splitlines():
        if line.startswith("@@"):
            sha, name, date, subject = line[2:].split("\t", 3)
            commits.append({"sha": sha, "author": name, "date": date, "message": subject[:200], "files": []})
        elif line.strip() and commits:
            commits[-1]["files"].append(line.strip())
            files[line.strip()] += 1
    return {"ref": ref, "commits": commits, "files": [{"path": f, "commits": c} for f, c in files.most_common(20)]}


# ---------------------------------------------------------------- sprint summary
def sprint_summary(repo_path: str, days: int = 14) -> dict:
    repo = git.Repo(repo_path)
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    raw = repo.git.log("--no-merges", f"--since={since}", "--name-only", "--format=@@%h\t%an\t%aI\t%s")
    by_issue, authors, modules, n = defaultdict(list), Counter(), Counter(), 0
    current = None
    for line in raw.splitlines():
        if line.startswith("@@"):
            sha, name, date, subject = line[2:].split("\t", 3)
            refs = ["#" + a if a else b for a, b in ISSUE_REF.findall(subject)] or ["(sin issue)"]
            current = {"sha": sha, "author": name, "date": date, "message": subject[:200]}
            for r in refs:
                by_issue[r].append(current)
            authors[name] += 1
            n += 1
        elif line.strip() and current:
            modules[_module(line.strip())] += 1
    groups = [{"issue": k, "commits": v} for k, v in sorted(by_issue.items(), key=lambda kv: (kv[0] == "(sin issue)", -len(kv[1])))]
    return {"days": days, "commits": n, "authors": [{"name": a, "commits": c} for a, c in authors.most_common()],
            "modules": [{"module": m, "changes": c} for m, c in modules.most_common(8)], "issues": groups}


# ---------------------------------------------------------------- anonymous usage per module (gaps and radar)
def usage_path(project_dir: Path) -> Path:
    return project_dir / "usage.json"


def record_usage(project_dir: Path, sources: list, answer: str) -> None:
    """Counts, per module, how many questions it answered and how many times Beacon said it had no information.
    Neither the question nor the person is stored."""
    try:
        data = json.loads(usage_path(project_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {"modules": {}, "no_info": 0, "questions": 0}
    data["questions"] += 1
    refused = bool(NO_INFO.search(answer))
    if refused:
        data["no_info"] += 1
    for mod in {_module(s["file_path"]) for s in sources if s.get("file_path") and s.get("chunk_type") != "overview"}:
        m = data["modules"].setdefault(mod, {"questions": 0, "no_info": 0})
        m["questions"] += 1
        m["no_info"] += int(refused)
    data["updated"] = time.time()
    usage_path(project_dir).parent.mkdir(parents=True, exist_ok=True)
    usage_path(project_dir).write_text(json.dumps(data), encoding="utf-8")


def insights(project_dir: Path, docs_dir: Path, reviews: Optional[dict]) -> dict:
    """Documentation gaps (modules people ask about most, with few verified documents) and the radar of code that is
    hard to understand (modules where Beacon most often had no information or answers needed correcting)."""
    try:
        usage = json.loads(usage_path(project_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        usage = {"modules": {}, "no_info": 0, "questions": 0}
    reviews = reviews or {}
    docs_per_module, verified_per_module = Counter(), Counter()
    for f, status in reviews.items():
        docs_per_module[_module(f)] += 1
        verified_per_module[_module(f)] += int(status == "verified")
    rows = []
    for mod, u in usage["modules"].items():
        docs = docs_per_module.get(mod, 0)
        verified = verified_per_module.get(mod, 0)
        rows.append({"module": mod, "questions": u["questions"], "no_info": u["no_info"],
                     "no_info_rate": round(u["no_info"] / u["questions"], 2) if u["questions"] else 0,
                     "docs": docs, "verified": verified, "verified_rate": round(verified / docs, 2) if docs else 0})
    gaps = sorted([r for r in rows if r["questions"] >= 2], key=lambda r: (r["verified_rate"], -r["questions"]))[:8]
    radar = sorted([r for r in rows if r["questions"] >= 2 and r["no_info"] > 0], key=lambda r: (-r["no_info_rate"], -r["questions"]))[:8]
    return {"questions": usage["questions"], "no_info": usage["no_info"], "gaps": gaps, "radar": radar}
