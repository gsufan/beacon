"""Repository overview: hierarchical summaries built from the per-file documentation.

Open questions ("what does this project do?", "how is it organized?") do not resemble any single code
chunk, so similarity search cannot answer them. The overview gives the model the big picture: one summary
per folder, built bottom-up from the purpose of each documented file, and one for the whole project.
It is incremental: a folder is summarized again only when the text it is built from changed.
"""

import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

OVERVIEW_MD = "OVERVIEW.md"
OVERVIEW_STATE = ".overview.json"
STATE_VERSION = 1

MAX_PURPOSE_CHARS = 350
# Entries are shortened step by step until the text of a folder fits in the prompt.
PURPOSE_LEVELS = (MAX_PURPOSE_CHARS, 200, 120, 70)

PURPOSE_HEADING = re.compile(r"^##\s+Prop[oó]sito\s*$", re.IGNORECASE | re.MULTILINE)
NEXT_HEADING = re.compile(r"^##\s", re.MULTILINE)

LANGUAGE_INSTRUCTION = ("IMPORTANTE: escribe todo en español neutro (sin modismos regionales ni voseo), "
                        "aunque el código o sus comentarios estén en inglés. Mantén en su forma original "
                        "los nombres de archivos, funciones y clases.")

FOLDER_SYSTEM_PROMPT = """Eres un ingeniero de software senior. Recibes las descripciones de los archivos y las subcarpetas de UNA carpeta de un repositorio.
Escribe un único párrafo de 2 a 4 líneas que explique para qué sirve la carpeta y cómo se relacionan sus partes.
Basa todo en el texto dado; no inventes componentes ni tecnologías que no aparecen. Sin introducción ni listas.

""" + LANGUAGE_INSTRUCTION

PROJECT_SYSTEM_PROMPT = """Eres un ingeniero de software senior. Recibes las descripciones de las carpetas y los archivos de nivel superior de un repositorio, y la lista de sus lenguajes.
Escribe en Markdown, con exactamente estas secciones:

## Qué hace
2 a 4 líneas, concretas, sobre el propósito del proyecto.

## Componentes principales
Una línea por carpeta o servicio relevante: `ruta/` — para qué sirve.

## Tecnologías
Solo las que aparecen en las descripciones o en la lista de lenguajes.

El propósito del proyecto es lo que hace la mayor parte de su código: usa la cantidad de archivos de cada carpeta como pista; los scripts y las herramientas auxiliares no definen el proyecto.
Basa todo en el texto dado; no inventes. Directo y técnico, sin relleno.

""" + LANGUAGE_INSTRUCTION

Llm = Callable[[str, str], str]


def extract_purpose(markdown: str) -> str:
    """The "Propósito" section of a per-file document (or its first lines if it has none)."""
    m = PURPOSE_HEADING.search(markdown)
    if m:
        rest = markdown[m.end():]
        n = NEXT_HEADING.search(rest)
        text = rest[:n.start()] if n else rest
    else:
        text = "\n".join(l for l in markdown.splitlines() if l.strip() and not l.startswith(("#", ">")))
    return re.sub(r"\s+", " ", text).strip()


def _shorten(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def collect_file_purposes(docs_dir: Path) -> Dict[str, str]:
    """file path (as documented) -> purpose, for every per-file document."""
    out = {}
    for p in sorted(docs_dir.rglob("*.md")):
        rel = p.relative_to(docs_dir).as_posix()
        if rel == OVERVIEW_MD:
            continue
        purpose = extract_purpose(p.read_text(encoding="utf-8"))
        if purpose:
            out[rel[:-len(".md")]] = purpose
    return out


def _tree(files: Dict[str, str]) -> Tuple[Dict[str, List[str]], Dict[str, List[str]]]:
    """folder -> files, folder -> subfolders; "" is the root."""
    dirs: Dict[str, List[str]] = {"": []}
    subdirs: Dict[str, List[str]] = {"": []}
    for path in files:
        parent = path.rsplit("/", 1)[0] if "/" in path else ""
        dirs.setdefault(parent, []).append(path)
        subdirs.setdefault(parent, [])
        d = parent
        while d:
            up = d.rsplit("/", 1)[0] if "/" in d else ""
            dirs.setdefault(up, [])
            subdirs.setdefault(up, [])
            if d not in subdirs[up]:
                subdirs[up].append(d)
            d = up
    return dirs, subdirs


def _folder_text(name: str, files: List[Tuple[str, str]], subs: List[Tuple[str, str, int]], budget: int) -> str:
    """Prompt text of a folder; the entries are shortened until it fits in `budget` characters."""
    for lim in PURPOSE_LEVELS:
        lines = [f"Carpeta: {name or '(raíz del repositorio)'}", "Archivos:"]
        lines += [f"- `{n}`: {_shorten(p, lim)}" for n, p in files] or ["- (ninguno)"]
        lines += ["Subcarpetas:"] + ([f"- `{n}/` ({c} archivos): {_shorten(s, lim)}" for n, s, c in subs] or ["- (ninguna)"])
        text = "\n".join(lines)
        if len(text) <= budget:
            return text
    return text[:budget - 60] + "\n[... lista recortada por longitud ...]"


def _hash(text: str) -> str:
    return hashlib.sha1((str(STATE_VERSION) + text).encode("utf-8")).hexdigest()


def _language_stats(files: Dict[str, str]) -> str:
    counts = Counter(Path(p).suffix.lstrip(".") or "(sin extensión)" for p in files)
    return ", ".join(f"{ext} ({n})" for ext, n in counts.most_common(8))


def load_state(docs_dir: Path) -> dict:
    p = docs_dir / OVERVIEW_STATE
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if data.get("version") == STATE_VERSION:
                return data
        except (OSError, ValueError):
            pass
    return {"version": STATE_VERSION, "dirs": {}, "root": {}}


def build_overview(docs_dir: Path, llm: Llm, budget_chars: int, on_step: Optional[Callable[[str], None]] = None) -> dict:
    """Builds or updates the overview from the per-file documents. Returns counts of folders summarized and reused."""
    files = collect_file_purposes(docs_dir)
    if not files:
        for name in (OVERVIEW_MD, OVERVIEW_STATE):
            (docs_dir / name).unlink(missing_ok=True)
        return {"carpetas_resumidas": 0, "carpetas_reutilizadas": 0}
    old = load_state(docs_dir)
    dirs, subdirs = _tree(files)
    summaries: Dict[str, str] = {}
    state_dirs: Dict[str, dict] = {}
    done, reused = 0, 0

    def count(d: str) -> int:
        return sum(1 for p in files if p.startswith(d + "/"))

    def depth(d: str) -> int:
        return d.count("/") + 1 if d else 0

    for d in sorted((d for d in dirs if d), key=depth, reverse=True):
        f = [(Path(p).name, files[p]) for p in sorted(dirs[d])]
        s = [(Path(c).name, summaries[c], count(c)) for c in sorted(subdirs[d])]
        if (len(f) == 1 and not s) or (not f and len(s) == 1):
            # A single file or a single subfolder: its text is already the folder's summary.
            summaries[d] = _shorten(f[0][1] if f else s[0][1], MAX_PURPOSE_CHARS)
            state_dirs[d] = {"hash": _hash(summaries[d]), "summary": summaries[d]}
            continue
        text = _folder_text(d, f, s, budget_chars)
        h = _hash(text)
        prev = old["dirs"].get(d)
        if prev and prev.get("hash") == h:
            summaries[d] = prev["summary"]
            reused += 1
        else:
            if on_step:
                on_step(d)
            summaries[d] = re.sub(r"\s+", " ", llm(FOLDER_SYSTEM_PROMPT, text)).strip()
            done += 1
        state_dirs[d] = {"hash": h, "summary": summaries[d]}

    root_files = [(Path(p).name, files[p]) for p in sorted(dirs[""])]
    root_subs = [(Path(c).name, summaries[c], count(c)) for c in sorted(subdirs[""])]
    root_text = _folder_text("", root_files, root_subs, budget_chars - 200)
    root_text += f"\nLenguajes (extensión y cantidad de archivos): {_language_stats(files)}"
    h = _hash(root_text)
    if old["root"].get("hash") == h:
        root_summary = old["root"]["summary"]
        reused += 1
    else:
        if on_step:
            on_step("")
        root_summary = llm(PROJECT_SYSTEM_PROMPT, root_text).strip()
        done += 1

    state = {"version": STATE_VERSION, "dirs": state_dirs, "root": {"hash": h, "summary": root_summary}}
    (docs_dir / OVERVIEW_STATE).write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    (docs_dir / OVERVIEW_MD).write_text(render_markdown(root_summary, {d: v["summary"] for d, v in state_dirs.items()}), encoding="utf-8")
    return {"carpetas_resumidas": done, "carpetas_reutilizadas": reused}


def render_markdown(root_summary: str, dir_summaries: Dict[str, str]) -> str:
    out = ["# Resumen del proyecto", "", "> Generado automáticamente a partir de la documentación de cada archivo.", "", root_summary.strip(), "",
           "## Estructura por carpetas", ""]
    for d in sorted(dir_summaries):
        out += [f"### `{d}/`", "", dir_summaries[d], ""]
    return "\n".join(out)


class RepoOverview:
    """Read access to the overview of a project, for answering open questions."""

    def __init__(self, docs_dir: Path):
        self.state = load_state(Path(docs_dir)) if (Path(docs_dir) / OVERVIEW_STATE).exists() else None

    @property
    def exists(self) -> bool:
        return bool(self.state and self.state["root"].get("summary"))

    def blocks(self, max_chars: int) -> List[Tuple[str, str]]:
        """(label, text) pairs in priority order: the project summary, then folders from the top level down."""
        if not self.exists:
            return []
        out, used = [("resumen del proyecto", self.state["root"]["summary"].strip())], len(self.state["root"]["summary"])
        for d in sorted(self.state["dirs"], key=lambda x: (x.count("/"), x)):
            text = self.state["dirs"][d]["summary"]
            if used + len(text) > max_chars:
                break
            out.append((f"resumen de la carpeta {d}", text))
            used += len(text)
        return out
