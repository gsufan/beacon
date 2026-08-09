"""Purga puntual de código generado/vendor ya indexado (normalmente no hace
falta: 'deuda-tecnica sync' ya la aplica automáticamente).
Uso: python tools/purge_generated.py <project_id> [--confirm]"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.engine.chroma_utils import get_chroma_client
from core.projects import get_project
from core.engine.indexer import COLLECTION_NAME
from core.engine.git_watcher import _is_generated_or_vendor


def purge(project_id: str, dry_run: bool = True):
    project = get_project(project_id)
    client = get_chroma_client(project.chroma_dir)
    collection = client.get_collection(COLLECTION_NAME)

    data = collection.get(include=["metadatas"])
    to_delete = [cid for cid, m in zip(data["ids"], data["metadatas"])
                 if _is_generated_or_vendor(m.get("file_path", ""))]

    print(f"Chunks generados/vendor encontrados: {len(to_delete)}")
    if not to_delete:
        return
    if dry_run:
        print("[DRY RUN] Corre con --confirm para aplicar.")
        return
    collection.delete(ids=to_delete)
    print(f"Purgados {len(to_delete)} chunks.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python tools/purge_generated.py <project_id> [--confirm]")
        sys.exit(1)
    purge(sys.argv[1], dry_run="--confirm" not in sys.argv)
