"""Call-graph expansion: if a retrieved function calls another indexed one, that one joins the context."""

import dataclasses
import logging
import re
from typing import Callable, List, Optional

from core.engine.retrieval import RetrievedChunk
from core.engine.scope_resolution import resolve_candidate

logger = logging.getLogger("beacon.rag")

DEFAULT_MAX_CALL_EXPANSIONS = 5

# Call candidates: `name(`, unless it is a definition (`def hook(` is not a call).
# Extracting calls from the AST would give almost the same result without per-language rules.
CALL_CANDIDATE_PATTERN = re.compile(
    r"(?<!\bdef )(?<!\bfunc )(?<!\bfunction )(?<!\bclass )\b([A-Za-z_][A-Za-z0-9_]*)\s*\(")
KEYWORD_BLOCKLIST = {
    "if", "for", "while", "switch", "catch", "return", "func", "def", "class",
    "new", "delete", "print", "println", "printf", "fmt", "string", "int",
    "float", "double", "bool", "var", "let", "const", "public", "private",
    "protected", "static", "void", "struct", "interface", "try", "except",
    "finally", "with", "case", "default", "else", "elif", "match", "async",
    "await", "yield", "throw", "raise", "super", "this", "self", "import",
    "from", "package", "namespace", "using", "typeof", "instanceof", "sizeof",
    "make", "len", "append", "range", "in", "is", "and", "or", "not", "lambda",
    "require", "module", "export", "type", "enum", "impl", "fn",
}


def extract_call_candidates(code: str, own_name: str) -> List[str]:
    candidates, seen = [], set()
    for match in CALL_CANDIDATE_PATTERN.finditer(code):
        name = match.group(1)
        if name in KEYWORD_BLOCKLIST or name == own_name or name in seen or len(name) <= 2:
            continue
        seen.add(name)
        candidates.append(name)
    return candidates


class CallGraphExpander:
    """`chunks_named` returns the indexed chunks with a given name (see Retriever.chunks_named)."""

    def __init__(self, chunks_named: Callable[[str], List[RetrievedChunk]]):
        self.chunks_named = chunks_named

    def _resolve(self, name: str, caller_file_path: str) -> Optional[RetrievedChunk]:
        try:
            matches = self.chunks_named(name)
        except Exception:  # noqa: BLE001 (the expansion is optional: the answer goes on without it)
            logger.warning("No se pudo buscar '%s' para el grafo de llamadas", name, exc_info=True)
            return None
        resolved = resolve_candidate({name: [(c.file_path, str(i)) for i, c in enumerate(matches)]}, name, caller_file_path)
        if resolved is None:
            return None
        return dataclasses.replace(matches[int(resolved)], expanded=True)

    def expand(self, chunks: List[RetrievedChunk], max_expansions: int = DEFAULT_MAX_CALL_EXPANSIONS) -> List[RetrievedChunk]:
        """The chunks called by the functions and methods in `chunks` that are not already there."""
        existing_names = {c.name for c in chunks if c.name}
        expanded: List[RetrievedChunk] = []
        for chunk in chunks:
            if chunk.chunk_type not in ("function", "method"):
                continue
            for candidate in extract_call_candidates(chunk.code, chunk.name):
                if len(expanded) >= max_expansions:
                    return expanded
                if candidate in existing_names:
                    continue
                found = self._resolve(candidate, chunk.file_path)
                if found:
                    expanded.append(found)
                    existing_names.add(candidate)
        return expanded
