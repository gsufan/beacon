"""
Limpieza única del índice existente: elimina los chunks que pertenecen a
código generado o de terceros (protobuf/gRPC stubs, vendor, node_modules,
etc.) que ya quedaron indexados ANTES de este fix. No requiere re-embeber
nada — solo borra lo que ya no queremos ahí.

Uso:
    python purge_generated.py            # dry-run: solo muestra qué borraría
    python purge_generated.py --confirm  # borra de verdad
"""

import os
import sys
from dotenv import load_dotenv
import chromadb

from git_watcher import _is_generated_or_vendor

load_dotenv()

CHROMA_DIR = os.getenv("CHROMA_PERSIST_DIR", "./data/chroma_db")
COLLECTION_NAME = os.getenv("CHROMA_COLLECTION_NAME", "codebase_index")


def purge_generated(dry_run: bool = True):
    client = chromadb.PersistentClient(path=CHROMA_DIR)
    collection = client.get_collection(COLLECTION_NAME)

    total_before = collection.count()
    data = collection.get(include=["metadatas"])

    to_delete = []
    affected_files = set()
    for chunk_id, meta in zip(data["ids"], data["metadatas"]):
        file_path = meta.get("file_path", "")
        if _is_generated_or_vendor(file_path):
            to_delete.append(chunk_id)
            affected_files.add(file_path)

    print(f"Total chunks en el índice: {total_before}")
    print(f"Chunks de código generado/vendor encontrados: {len(to_delete)}")
    print(f"Archivos afectados: {len(affected_files)}")
    for f in sorted(affected_files)[:15]:
        print(f"  - {f}")
    if len(affected_files) > 15:
        print(f"  ... y {len(affected_files) - 15} más")

    if not to_delete:
        print("\nNada que purgar.")
        return

    if dry_run:
        print(f"\n[DRY RUN] No se borró nada. Corre con --confirm para aplicar la purga real.")
        return

    # Borrar en lotes por si son muchos ids
    BATCH_SIZE = 200
    for i in range(0, len(to_delete), BATCH_SIZE):
        collection.delete(ids=to_delete[i:i + BATCH_SIZE])

    print(f"\nListo. Se purgaron {len(to_delete)} chunks generados/vendor.")
    print(f"Chunks restantes en el índice: {collection.count()}")
    print("\nRecomendación: corre 'python indexer.py <repo>' de nuevo para que "
          "el commit actual quede marcado como el último indexado limpio "
          "(evita confusión en la próxima sincronización incremental).")


if __name__ == "__main__":
    confirm = "--confirm" in sys.argv
    purge_generated(dry_run=not confirm)
