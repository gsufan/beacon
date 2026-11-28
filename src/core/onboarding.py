"""Onboarding route for someone new to a project: which files to read first (those the rest of the code depends on
most), a glossary of the domain, and the open issues with the smallest impact radius to start with.

The order comes from the index: how many other files call into each file. It needs no model; the glossary does.
"""

import re
from collections import Counter, defaultdict
from pathlib import Path

from core.engine.call_graph import extract_call_candidates
from core.engine.overview import OVERVIEW_MD, collect_file_purposes
from core.engine.retrieval import TEST_PATH_PATTERN

MAX_CHUNKS = 8000
MAX_FILES = 8
MAX_ISSUES = 12
AMBIGUOUS = 3  # a name defined in more files than this says nothing about who is called
# Method names shared with the standard library (dict.items(), file.close()...): a call says nothing about who is called.
GENERIC = {"items", "copy", "update", "close", "get", "set", "read", "write", "keys", "values", "pop", "append", "extend",
           "add", "remove", "clear", "send", "open", "run", "start", "stop", "join", "split", "format", "lower", "upper",
           "strip", "encode", "decode", "replace", "sort", "index", "count", "find", "next", "iter", "call", "apply", "load",
           "dump", "dumps", "loads", "parse", "init", "main", "setup", "reset", "flush", "emit", "render", "handle", "process"}
EASY_LABELS = re.compile(r"good.?first|first.?timers?|beginner|easy|starter|docs?|documentation|principiante|f[aá]cil", re.IGNORECASE)


def call_map(collection) -> dict:
    """For every source file: which other files call into it; for every function: which functions call it."""
    res = collection.get(include=["metadatas", "documents"], limit=MAX_CHUNKS)
    rows = [(m, d) for m, d in zip(res["metadatas"], res["documents"]) if m.get("chunk_type") != "overview"]
    defined = defaultdict(set)
    for m, _ in rows:
        if m.get("name") and m.get("chunk_type") in ("function", "method", "class"):
            defined[m["name"]].add(m["file_path"])
    callers_of_file, callers_of_name, chunks_per_file = defaultdict(set), defaultdict(set), Counter()
    for m, code in rows:
        caller = m.get("file_path", "")
        chunks_per_file[caller] += 1
        for name in extract_call_candidates(code or "", m.get("name") or ""):
            targets = defined.get(name, ())
            if name in GENERIC or not targets or len(targets) > AMBIGUOUS:
                continue
            for target in targets:
                callers_of_name[(target, name)].add((caller, m.get("name") or ""))
                if target != caller and not TEST_PATH_PATTERN.search(caller):
                    callers_of_file[target].add(caller)
    return {"callers_of_file": callers_of_file, "callers_of_name": callers_of_name, "chunks_per_file": chunks_per_file}


def callers(cmap: dict, file_path: str, name: str) -> int:
    """Functions outside the tests that call this one."""
    return sum(1 for f, _ in cmap["callers_of_name"].get((file_path, name), ()) if not TEST_PATH_PATTERN.search(f))


def reading_order(cmap: dict, docs_dir: Path, limit: int = MAX_FILES) -> list:
    """Source files most depended on first, with their purpose (from the documentation) and their key functions."""
    purposes = collect_file_purposes(docs_dir) if docs_dir.exists() else {}
    files = [f for f in cmap["chunks_per_file"] if f and not TEST_PATH_PATTERN.search(f)]
    ranked = sorted(files, key=lambda f: (-len(cmap["callers_of_file"].get(f, ())), -cmap["chunks_per_file"][f], f))
    out = []
    for f in ranked[:limit]:
        key = sorted(((callers(cmap, f, n), n) for (t, n) in cmap["callers_of_name"] if t == f), reverse=True)[:3]
        out.append({"file_path": f, "callers": len(cmap["callers_of_file"].get(f, ())), "purpose": purposes.get(f, ""),
                    "key_functions": [{"name": n, "callers": c} for c, n in key]})
    return out


def _radius(n: int) -> str:
    return "small" if n <= 2 else "medium" if n <= 8 else "large"


def first_issues(retriever, cmap: dict, issues: list, limit: int = MAX_ISSUES) -> list:
    """Open issues ranked to start with: the code they touch has few callers, and easy labels help."""
    out = []
    for issue in issues[:limit]:
        text = f"{issue.get('title', '')}\n{(issue.get('body') or '')[:600]}"
        try:
            chunks = [c for c in retriever.retrieve(text, top_k=6)
                      if c.chunk_type in ("function", "method", "class") and not TEST_PATH_PATTERN.search(c.file_path)][:3]
        except Exception:  # noqa: BLE001 - an issue without retrievable code is simply ranked last
            chunks = []
        n = callers(cmap, chunks[0].file_path, chunks[0].name) if chunks else None  # the most relevant function
        labels = issue.get("labels") or []
        easy = any(EASY_LABELS.search(label) for label in labels)
        score = (n if n is not None else 99) - (5 if easy else 0)
        out.append({**{k: issue.get(k) for k in ("id", "ref", "title", "url", "source")}, "labels": labels, "easy_label": easy,
                    "callers": n, "radius": _radius(n) if n is not None else "unknown",
                    "files": list(dict.fromkeys(c.file_path for c in chunks)), "functions": [c.name for c in chunks if c.name][:3],
                    "_score": score})
    out.sort(key=lambda i: i["_score"])
    for i in out:
        i.pop("_score")
    return out


def overview_text(docs_dir: Path, limit: int = 3000) -> str:
    p = docs_dir / OVERVIEW_MD
    try:
        return p.read_text(encoding="utf-8")[:limit]
    except OSError:
        return ""
