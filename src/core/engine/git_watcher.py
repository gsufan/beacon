"""
Git Watcher — Pilar 4B de la propuesta.

Detecta qué archivos cambiaron entre dos commits (o entre el último commit
indexado y el estado actual del working directory) para que el indexador
NO tenga que re-procesar todo el repositorio en cada actualización.

Estrategia:
- Se guarda el hash del último commit indexado (en ChromaDB, como metadata
  de una colección de control, o en un archivo de estado local).
- Cada vez que se pide indexar, se compara ese hash contra HEAD.
- Se devuelve una lista de archivos: añadidos, modificados, eliminados,
  renombrados — cada categoría se maneja distinto en el indexador:
    * añadido/modificado -> re-chunkear y re-embeber
    * eliminado -> purgar sus chunks de ChromaDB
    * renombrado -> purgar los chunks bajo el nombre viejo, re-indexar bajo el nuevo
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import git


# Extensiones que nos interesa indexar (evita procesar binarios, imágenes, etc.)
INDEXABLE_EXTENSIONS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".go",
    ".cs", ".cpp", ".cc", ".hpp", ".c", ".h", ".rs", ".php",
}

# Fragmentos de ruta que indican código GENERADO automáticamente (protobuf,
# gRPC stubs, vendor libs, node_modules, builds). Este código no tiene valor
# semántico para el desarrollador (es boilerplate mecánico repetido en cada
# servicio que consume el proto) y solo diluye la calidad de la búsqueda al
# competir por espacio en el top-k contra la lógica de negocio real. Se
# excluye desde el origen, no solo se ignora en la UI.
EXCLUDED_PATH_MARKERS = [
    "_pb2.py", "_pb2_grpc.py",       # stubs generados de protobuf/gRPC en Python
    ".pb.go", "_grpc.pb.go",         # stubs generados en Go
    ".pb.h", ".pb.cc",               # stubs generados en C++
    "genproto/", "/generated/", "/gen/",
    "vendor/", "node_modules/", "/dist/", "/build/",
    ".min.js",                        # JS minificado, no legible/parseable con sentido
]


def _is_generated_or_vendor(path: str) -> bool:
    normalized = path.replace("\\", "/")
    return any(marker in normalized for marker in EXCLUDED_PATH_MARKERS)


@dataclass
class ChangeSet:
    added: List[str] = field(default_factory=list)
    modified: List[str] = field(default_factory=list)
    deleted: List[str] = field(default_factory=list)
    renamed: List[tuple] = field(default_factory=list)  # (old_path, new_path)
    # True cuando no hay un commit base válido y se listó el repo completo: lo
    # que ya estaba en el índice puede ser de archivos que ya no existen.
    full_rescan: bool = False

    def files_to_reindex(self) -> List[str]:
        """Archivos que hay que (re)chunkear y (re)embeber."""
        return self.added + self.modified + [new for _, new in self.renamed]

    def files_to_purge(self) -> List[str]:
        """Archivos cuyos vectores viejos hay que eliminar de ChromaDB."""
        return self.deleted + [old for old, _ in self.renamed]

    def is_empty(self) -> bool:
        return not (self.added or self.modified or self.deleted or self.renamed)

    def summary(self) -> str:
        return (f"+{len(self.added)} añadidos, "
                f"~{len(self.modified)} modificados, "
                f"-{len(self.deleted)} eliminados, "
                f"→{len(self.renamed)} renombrados")


def _is_indexable(path: str) -> bool:
    return Path(path).suffix.lower() in INDEXABLE_EXTENSIONS and not _is_generated_or_vendor(path)


def _classify(changes: "ChangeSet", diff_item) -> None:
    """Agrega un item de diff de GitPython a la categoría que corresponde."""
    kind, old, new = diff_item.change_type, diff_item.a_path, diff_item.b_path
    if kind in ("A", "C"):
        if _is_indexable(new):
            changes.added.append(new)
    elif kind in ("M", "T"):
        if _is_indexable(new):
            changes.modified.append(new)
    elif kind == "D":
        if _is_indexable(old):
            changes.deleted.append(old)
    elif kind == "R":
        # Si el rename cruza la frontera de lo indexable (ej. .py -> .txt, o a
        # vendor/), se trata como borrado o alta para no dejar chunks huérfanos.
        old_ok, new_ok = _is_indexable(old), _is_indexable(new)
        if old_ok and new_ok:
            changes.renamed.append((old, new))
        elif old_ok:
            changes.deleted.append(old)
        elif new_ok:
            changes.added.append(new)


class GitWatcher:
    def __init__(self, repo_path: str):
        self.repo = git.Repo(repo_path)
        self.repo_path = repo_path

    def current_commit_hash(self) -> str:
        return self.repo.head.commit.hexsha

    def get_changes_since(self, last_indexed_commit: Optional[str]) -> ChangeSet:
        """
        Compara el commit ya indexado contra HEAD.
        Si last_indexed_commit es None (primera vez), se considera que
        TODO el repositorio está "añadido" (indexación inicial completa).
        """
        head = self.repo.head.commit
        changes = ChangeSet()

        if last_indexed_commit is None:
            changes.full_rescan = True
            for item in head.tree.traverse():
                if item.type == "blob" and _is_indexable(item.path):
                    changes.added.append(item.path)
            return changes

        try:
            old_commit = self.repo.commit(last_indexed_commit)
            old_commit.tree  # con un sha completo, repo.commit() no verifica que el objeto exista
        except (git.BadName, git.BadObject, ValueError):
            # El commit guardado ya no existe (ej: rebase, historia reescrita).
            # Forzamos re-indexación completa en vez de fallar silenciosamente.
            return self.get_changes_since(None)

        for diff_item in old_commit.diff(head):
            _classify(changes, diff_item)
        return changes

    def get_uncommitted_changes(self) -> ChangeSet:
        """
        Cambios en el working directory que AÚN no se han commiteado
        (útil para indexar mientras se desarrolla, sin esperar a un commit).
        """
        changes = ChangeSet()

        # Working dir vs HEAD: incluye archivos nuevos ya agregados al index y renames
        for diff_item in self.repo.head.commit.diff(None):
            _classify(changes, diff_item)

        # Archivos nuevos sin trackear
        for path in self.repo.untracked_files:
            if _is_indexable(path):
                changes.added.append(path)

        return changes


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Uso: python git_watcher.py <ruta_repo> [commit_hash_anterior]")
        sys.exit(1)

    repo_path = sys.argv[1]
    last_commit = sys.argv[2] if len(sys.argv) > 2 else None

    watcher = GitWatcher(repo_path)
    print(f"HEAD actual: {watcher.current_commit_hash()}")

    changes = watcher.get_changes_since(last_commit)
    print(f"\nCambios desde {'(inicio)' if last_commit is None else last_commit[:8]}:")
    print(f"  {changes.summary()}")
    print(f"  Añadidos: {changes.added}")
    print(f"  Modificados: {changes.modified}")
    print(f"  Eliminados: {changes.deleted}")
    print(f"  Renombrados: {changes.renamed}")

    uncommitted = watcher.get_uncommitted_changes()
    if not uncommitted.is_empty():
        print(f"\nCambios sin commitear: {uncommitted.summary()}")
