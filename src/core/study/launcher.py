"""Preparation and cleanup around a `beacon study` session, done by the console before and after the study server.

The study runs on its own configuration (data/_study/config.yaml) with the model server and models already
configured, so it never touches the user's projects.
"""

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

import yaml

from core.config import default_ai_provider, load_config
from core.fs import remove_tree
from core.projects import DATA_ROOT
from core.study import hardware
from core.study.session import StudySession

_STUDY_CLONE = re.compile(r"estudio-[0-9a-f]{4}")
STUDY_PROJECT_ID = "estudio"  # prefix of the study repositories; also the folder of older versions


def study_dir() -> Path:
    return DATA_ROOT / "_study"


def session_path() -> Path:
    return study_dir() / "sesion.json"


def study_project_ids() -> list:
    """Ids of the study repositories registered in its own configuration (the most recent last)."""
    try:
        raw = yaml.safe_load((study_dir() / "config.yaml").read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return []
    return [p["id"] for p in raw.get("projects") or [] if str(p.get("id", "")).startswith(STUDY_PROJECT_ID)]


def remove_orphan_clones(keep: Iterable[str] = ()):
    """Deletes study clones left behind by a process killed mid-clone. Only folders named like a study clone that
    are not one of the user's own projects are touched."""
    try:
        own = {p.id for p in load_config().projects}
    except Exception:  # noqa: BLE001
        own = set()
    keep = set(keep)
    for folder in DATA_ROOT.glob("estudio-*"):
        if _STUDY_CLONE.fullmatch(folder.name) and folder.name not in own and folder.name not in keep:
            remove_tree(folder, ignore_errors=True)


def remove_study_data():
    for project_id in study_project_ids() + [STUDY_PROJECT_ID]:
        remove_tree(DATA_ROOT / project_id, ignore_errors=True)
    remove_orphan_clones()


@dataclass
class StudyLaunch:
    session: StudySession
    resumed: bool
    config_path: Path

    def server_env(self) -> dict:
        return dict(os.environ, BEACON_STUDY="1", DEUDA_TECNICA_CONFIG=str(self.config_path))


def _write_study_config(config_path: Path, resumed: bool) -> list:
    """Study configuration with the model server of the user's configuration (or the defaults). A resumed
    session keeps the repository it had prepared."""
    try:
        ai = load_config().ai_provider
    except FileNotFoundError:
        ai = default_ai_provider()
    projects = []
    if config_path.exists() and resumed:
        try:
            projects = (yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}).get("projects") or []
        except yaml.YAMLError:
            projects = []
    ai_cfg = {"provider": ai.provider, "host": ai.host, "embedding_model": ai.embedding_model, "llm_model": ai.llm_model}
    config_path.write_text(yaml.safe_dump({"ai_provider": ai_cfg, "projects": projects}, allow_unicode=True), encoding="utf-8")
    return projects


def prepare(*, profile: str, code: Optional[str], order: int, language: str, beacon_version: str, new: bool) -> StudyLaunch:
    """Starts or resumes the session and writes the study configuration. Raises StudyError for invalid options."""
    study_dir().mkdir(parents=True, exist_ok=True)
    session = StudySession(session_path())
    if new:
        discard(session)
    resumed = session.started
    if not resumed:
        session.start(profile=profile, code=code, order=order, language=language,
                      beacon_version=beacon_version, beacon_commit=hardware.beacon_commit())
    config_path = study_dir() / "config.yaml"
    projects = _write_study_config(config_path, resumed)
    remove_orphan_clones(keep=[p["id"] for p in projects])
    return StudyLaunch(session=session, resumed=resumed, config_path=config_path)


def session_in_progress() -> bool:
    """Read again from disk: the study server may have discarded the session while it ran."""
    return StudySession(session_path()).started


def discard(session: StudySession):
    session.discard()
    remove_study_data()
