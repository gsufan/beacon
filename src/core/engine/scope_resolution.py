"""Desambiguación por scope al resolver un nombre de llamada a un chunk concreto.

Usado por rag_engine.py para la expansión por grafo de llamadas al armar
el contexto del RAG.
"""

from typing import Dict, List, Optional, Tuple


def _scope_prefix(file_path: str) -> str:
    return "/".join(file_path.split("/")[:2])


def resolve_candidate(
    candidates_by_name: Dict[str, List[Tuple[str, str]]],
    name: str,
    caller_file_path: str,
) -> Optional[str]:
    """Resuelve `name` a un id de nodo/chunk usando `candidates_by_name`.

    `candidates_by_name` mapea name -> [(file_path, node_id), ...].
    Si hay un solo candidato, se usa directo. Si hay varios, se desambigua
    por scope (mismos primeros dos niveles de carpeta que el llamador);
    si sigue siendo ambiguo, no se resuelve.
    """
    matches = candidates_by_name.get(name)
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0][1]
    scope_prefix = _scope_prefix(caller_file_path)
    same_scope = [node_id for file_path, node_id in matches if file_path.startswith(scope_prefix)]
    if len(same_scope) != 1:
        return None
    return same_scope[0]
