"""Migración única: copia embeddings ya calculados a métrica coseno, sin re-embeber.
Uso: python tools/migrate_to_cosine.py <project_id>"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.engine.chroma_utils import bump_index_version, get_chroma_client
from core.projects import get_project
from core.engine.indexer import COLLECTION_NAME

BATCH_SIZE = 200


def migrate(project_id: str):
    project = get_project(project_id)
    client = get_chroma_client(project.chroma_dir)

    try:
        old = client.get_collection(COLLECTION_NAME)
    except Exception:
        print(f"No existe la colección de '{project_id}'. Nada que migrar.")
        return

    if (old.metadata or {}).get("hnsw:space") == "cosine":
        print("Ya usa métrica coseno.")
        return

    total = old.count()
    if total == 0:
        print("Colección vacía.")
        return

    print(f"Migrando {total} chunks de '{project_id}' a métrica coseno...")
    data = old.get(include=["documents", "metadatas", "embeddings"])

    temp_name = f"{COLLECTION_NAME}_cosine_migration"
    try:
        client.delete_collection(temp_name)
    except Exception:
        pass
    new = client.create_collection(name=temp_name, metadata={"hnsw:space": "cosine"})

    for i in range(0, total, BATCH_SIZE):
        end = min(i + BATCH_SIZE, total)
        new.add(ids=data["ids"][i:end], documents=data["documents"][i:end],
                metadatas=data["metadatas"][i:end], embeddings=data["embeddings"][i:end])
        print(f"  {end}/{total}")

    client.delete_collection(COLLECTION_NAME)
    new.modify(name=COLLECTION_NAME)
    bump_index_version(Path(project.chroma_dir).parent)  # un servidor levantado recarga el índice
    print(f"Listo. {total} chunks migrados.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Uso: python tools/migrate_to_cosine.py <project_id>")
        sys.exit(1)
    migrate(sys.argv[1])
