"""Scope disambiguation when resolving a call name to a concrete chunk (call graph)."""

from typing import Dict, List, Optional, Tuple


def _scope_prefix(file_path: str) -> str:
    return "/".join(file_path.split("/")[:2])


def resolve_candidate(
    candidates_by_name: Dict[str, List[Tuple[str, str]]],
    name: str,
    caller_file_path: str,
) -> Optional[str]:
    """Resolves `name` to a node/chunk id using `candidates_by_name` (name -> [(file_path, node_id), ...]).
    A single candidate is used directly; with several, it is disambiguated by scope (same first two folder
    levels as the caller); if still ambiguous, it is not resolved.
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
