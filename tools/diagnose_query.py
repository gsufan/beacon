"""Diagnóstico: cuántos chunks hay, cuántos son generados, si un archivo está indexado.
Uso: python tools/diagnose_query.py <project_id>"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.engine.chroma_utils import get_chroma_client
from core.projects import get_project
from core.engine.indexer import COLLECTION_NAME
from core.engine.git_watcher import _is_generated_or_vendor


def diagnose(project_id: str):
    project = get_project(project_id)
    client = get_chroma_client(project.chroma_dir)
    collection = client.get_collection(COLLECTION_NAME)

    data = collection.get(include=["metadatas"])
    paths = [m.get("file_path", "?") for m in data["metadatas"]]
    generated = [p for p in paths if _is_generated_or_vendor(p)]

    print(f"Total chunks: {len(paths)}")
    print(f"Generados/vendor (no deberían quedar tras 'sync'): {len(generated)}")
    print(f"Archivos únicos indexados: {len(set(paths))}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Uso: python tools/diagnose_query.py <project_id>")
        sys.exit(1)
    diagnose(sys.argv[1])
