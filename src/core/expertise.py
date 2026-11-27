"""Who knows a part of the code: the authors who changed each file most, read from the local git history.

It only reads the repository's history (no network) and returns names and counts, not emails.
"""

from collections import Counter
from pathlib import PurePosixPath

import git

MAX_PATHS = 10
MAX_COMMITS = 300


class InvalidPathError(ValueError):
    pass


def _clean(path: str) -> str:
    p = PurePosixPath(path.replace("\\", "/"))
    if p.is_absolute() or ".." in p.parts or not p.parts:
        raise InvalidPathError(f"Ruta no válida: {path}")
    return str(p)


def experts(repo_path: str, paths: list[str], top: int = 3) -> dict:
    """Top authors per file and overall, by number of commits that touched each file."""
    paths = [_clean(p) for p in dict.fromkeys(paths)][:MAX_PATHS]
    repo = git.Repo(repo_path)
    per_file, overall, last_seen = [], Counter(), {}
    for path in paths:
        raw = repo.git.log("--no-merges", "--follow", f"-n{MAX_COMMITS}", "--format=%an\t%aI", "--", path)
        counts = Counter()
        for line in raw.splitlines():
            if "\t" not in line:
                continue
            name, date = line.split("\t", 1)
            counts[name] += 1
            last_seen[name] = max(last_seen.get(name, ""), date)
        overall.update(counts)
        per_file.append({"path": path, "commits": sum(counts.values()),
                         "authors": [{"name": n, "commits": c} for n, c in counts.most_common(top)]})
    return {
        "files": per_file,
        "overall": [{"name": n, "commits": c, "last": last_seen.get(n)} for n, c in overall.most_common(top)],
    }
