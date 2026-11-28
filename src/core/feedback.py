"""Thumbs up or down on answers, stored in Beacon Local without the name of who voted. A thumbs down (with an optional
comment) queues the answer for the tech lead to correct and verify; a thumbs up marks it as a candidate to verify as
it is. Either way, the corrected or verified answer is then offered to whoever asks something similar."""

import json
import time
import uuid
from pathlib import Path

MAX_ITEMS = 500


def _path(project_dir: Path) -> Path:
    return project_dir / "feedback.json"


def _load(project_dir: Path) -> list:
    try:
        return json.loads(_path(project_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _save(project_dir: Path, items: list) -> None:
    path = _path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def add(project_dir: Path, vote: str, question: str, answer: str, sources: list, comment: str = "") -> dict:
    """Records a vote. The same answer voted again adds to its count instead of creating another entry."""
    items = _load(project_dir)
    key = (question.strip(), answer.strip())
    for item in items:
        if item["status"] == "open" and item["vote"] == vote and (item["question"], item["answer"]) == key:
            item["count"] += 1
            if comment.strip():
                item["comments"] = (item["comments"] + [comment.strip()[:500]])[-5:]
            _save(project_dir, items)
            return item
    files = [{k: s.get(k) for k in ("file_path", "chunk_type", "name", "start_line", "end_line")} for s in sources[:12]]
    item = {"id": uuid.uuid4().hex[:12], "ts": time.time(), "vote": vote, "question": key[0], "answer": key[1], "sources": files,
            "comments": [comment.strip()[:500]] if comment.strip() else [], "count": 1, "status": "open"}
    items.append(item)
    # Open votes are never dropped; the oldest resolved ones go first when the file grows.
    excess = len(items) - MAX_ITEMS
    if excess > 0:
        old = {i["id"] for i in [i for i in items if i["status"] != "open"][:excess]}
        items = [i for i in items if i["id"] not in old]
    _save(project_dir, items)
    return item


def queue(project_dir: Path) -> dict:
    """Open votes for the tech lead: answers to correct (thumbs down) and candidates to verify (thumbs up), most voted
    first, plus totals."""
    items = _load(project_dir)
    open_items = sorted((i for i in items if i["status"] == "open"), key=lambda i: (-i["count"], -i["ts"]))
    return {"to_fix": [i for i in open_items if i["vote"] == "down"], "to_verify": [i for i in open_items if i["vote"] == "up"],
            "totals": {"up": sum(i["count"] for i in items if i["vote"] == "up"),
                       "down": sum(i["count"] for i in items if i["vote"] == "down"),
                       "resolved": sum(1 for i in items if i["status"] != "open")}}


def resolve(project_dir: Path, item_id: str, outcome: str) -> bool:
    """Closes a vote: «verified» when the tech lead verified (or corrected) the answer, «dismissed» otherwise."""
    items = _load(project_dir)
    for item in items:
        if item["id"] == item_id and item["status"] == "open":
            item["status"], item["resolved_ts"] = outcome, time.time()
            _save(project_dir, items)
            return True
    return False


def resolve_question(project_dir: Path, question: str) -> int:
    """Closes the open votes on a question that a tech lead just verified."""
    items, n = _load(project_dir), 0
    for item in items:
        if item["status"] == "open" and item["question"] == question.strip():
            item["status"], item["resolved_ts"] = "verified", time.time()
            n += 1
    if n:
        _save(project_dir, items)
    return n
