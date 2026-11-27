"""Knowledge verified by the team: generated documentation and answers that a tech lead reviewed (and possibly
corrected).

The content stays in Beacon Local; Beacon Hub only signs the verification (who, when and a digest of the content).
A verification is valid while the content still matches its digest; editing the text turns it back into a draft.
When the code behind a verified document changes, the new AI draft is written next to it instead of overwriting the
reviewed text, and the document is shown as pending review.
"""

import hashlib
import json
import math
import time
import uuid
from pathlib import Path
from typing import Optional

import git

from hub.security import InvalidTokenError, verify_token

ANSWER_MATCH = 0.86  # cosine similarity between questions for a verified answer to be offered


def digest(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


def _file(project_dir: Path, name: str) -> Path:
    return project_dir / name


def _load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _save(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------- documentation
def reviews_path(project_dir: Path) -> Path:
    return _file(project_dir, "doc_reviews.json")


def draft_path(doc_path: Path) -> Path:
    return doc_path.with_name(doc_path.name[: -len(".md")] + ".draft.md")


def has_review(project_dir: Path, file_path: str) -> bool:
    return file_path in _load(reviews_path(project_dir), {})


def record_doc(project_dir: Path, file_path: str, token: str, reviewer: str, corrected: bool) -> dict:
    reviews = _load(reviews_path(project_dir), {})
    reviews[file_path] = {"token": token, "reviewer": reviewer, "ts": time.time(), "corrected": corrected}
    _save(reviews_path(project_dir), reviews)
    return reviews[file_path]


def doc_status(project_dir: Path, docs_dir: Path, file_path: str, public_key: Optional[str]) -> dict:
    """draft (only the AI wrote it), verified, or pending (verified, but its text changed or the code produced a new
    draft since then)."""
    doc = docs_dir / f"{file_path}.md"
    rec = _load(reviews_path(project_dir), {}).get(file_path)
    has_draft = draft_path(doc).exists()
    if not rec or not doc.exists():
        return {"status": "draft"}
    info = {"reviewer": rec["reviewer"], "ts": rec["ts"], "corrected": rec.get("corrected", False), "new_draft": has_draft}
    valid = False
    if public_key:
        try:
            claims = verify_token(public_key, rec["token"], "verification")
            valid = claims["digest"] == digest(doc.read_text(encoding="utf-8")) and claims["ref"] == file_path
        except InvalidTokenError:
            valid = False
    info["status"] = "verified" if valid and not has_draft else "pending"
    return info


# ---------------------------------------------------------------- answers
def answers_path(project_dir: Path) -> Path:
    return _file(project_dir, "verified_answers.json")


def _blob(repo: Optional[git.Repo], path: str) -> Optional[str]:
    if repo is None:
        return None
    try:
        return repo.git.rev_parse(f"HEAD:{path}")
    except git.GitError:
        return None


def _repo(repo_path: str) -> Optional[git.Repo]:
    try:
        return git.Repo(repo_path)
    except (git.GitError, OSError):
        return None


def add_answer(project_dir: Path, repo_path: str, question: str, answer: str, sources: list, embedding: list,
               token: str, reviewer: str, corrected: bool, answer_id: Optional[str] = None) -> dict:
    repo = _repo(repo_path)
    files = sorted({s["file_path"] for s in sources if s.get("file_path") and s.get("chunk_type") != "overview"})
    item = {"id": answer_id or uuid.uuid4().hex[:12], "question": question, "answer": answer, "sources": sources[:12],
            "files": {f: _blob(repo, f) for f in files}, "embedding": embedding, "token": token, "reviewer": reviewer,
            "ts": time.time(), "corrected": corrected}
    items = _load(answers_path(project_dir), [])
    items.append(item)
    _save(answers_path(project_dir), items)
    return {k: v for k, v in item.items() if k != "embedding"}


def _cos(a: list, b: list) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _valid_answer(item: dict, public_key: Optional[str]) -> bool:
    if not public_key:
        return False
    try:
        claims = verify_token(public_key, item["token"], "verification")
    except InvalidTokenError:
        return False
    return claims["digest"] == digest(item["answer"]) and claims["ref"] == item["id"]


def stale_files(item: dict, repo_path: str) -> list:
    repo = _repo(repo_path)
    return [f for f, sha in item.get("files", {}).items() if sha and _blob(repo, f) != sha]


def match(project_dir: Path, repo_path: str, question_embedding: list, public_key: Optional[str]) -> Optional[dict]:
    """The verified answer whose question is closest to this one, if it is close enough and its signature is valid."""
    best, score = None, 0.0
    for item in _load(answers_path(project_dir), []):
        s = _cos(question_embedding, item.get("embedding") or [])
        if s > score:
            best, score = item, s
    if best is None or score < ANSWER_MATCH or not _valid_answer(best, public_key):
        return None
    return {"id": best["id"], "question": best["question"], "answer": best["answer"], "reviewer": best["reviewer"],
            "ts": best["ts"], "corrected": best.get("corrected", False), "similarity": round(score, 3),
            "stale_files": stale_files(best, repo_path)}


def list_answers(project_dir: Path, repo_path: str, public_key: Optional[str]) -> list:
    out = []
    for item in _load(answers_path(project_dir), []):
        out.append({"id": item["id"], "question": item["question"], "answer": item["answer"], "reviewer": item["reviewer"],
                    "ts": item["ts"], "corrected": item.get("corrected", False), "valid": _valid_answer(item, public_key),
                    "stale_files": stale_files(item, repo_path)})
    return sorted(out, key=lambda x: -x["ts"])


def remove_answer(project_dir: Path, answer_id: str) -> bool:
    items = _load(answers_path(project_dir), [])
    kept = [i for i in items if i["id"] != answer_id]
    _save(answers_path(project_dir), kept)
    return len(kept) != len(items)
