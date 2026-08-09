"""
Migración única: si ya indexaste tu repo ANTES de este fix (colección con
métrica L2 por defecto), este script copia todo lo ya calculado —
embeddings, documentos, metadatas— a una colección nueva con métrica
coseno, SIN volver a llamar a Ollama. Los vectores no cambian, solo cómo
se comparan entre sí al buscar.

Uso:
    python migrate_to_cosine.py

Después de correr esto, tu .env sigue apuntando a CHROMA_COLLECTION_NAME
normal (el script reemplaza la colección vieja por la nueva bajo el mismo
nombre), así que no hay que tocar nada más.
"""

import os
from dotenv import load_dotenv
import chromadb

load_dotenv()

CHROMA_DIR = os.getenv("CHROMA_PERSIST_DIR", "./data/chroma_db")
COLLECTION_NAME = os.getenv("CHROMA_COLLECTION_NAME", "codebase_index")
TEMP_NAME = f"{COLLECTION_NAME}_cosine_migration"

BATCH_SIZE = 200  # inserciones en lotes, por si el repo tiene miles de chunks


def migrate():
    client = chromadb.PersistentClient(path=CHROMA_DIR)

    try:
        old_collection = client.get_collection(COLLECTION_NAME)
    except Exception:
        print(f"No existe la colección '{COLLECTION_NAME}' en {CHROMA_DIR}. Nada que migrar.")
        return

    existing_metadata = old_collection.metadata or {}
    if existing_metadata.get("hnsw:space") == "cosine":
        print("La colección ya usa métrica coseno. No es necesario migrar.")
        return

    total = old_collection.count()
    if total == 0:
        print("La colección está vacía. Nada que migrar.")
        return

    print(f"Migrando {total} chunks de '{COLLECTION_NAME}' (L2) a métrica coseno...")

    # Traemos TODO, incluyendo los embeddings ya calculados (no se re-embebe nada)
    data = old_collection.get(include=["documents", "metadatas", "embeddings"])

    # Recreamos limpio por si quedó basura de una corrida previa fallida
    try:
        client.delete_collection(TEMP_NAME)
    except Exception:
        pass

    new_collection = client.create_collection(
        name=TEMP_NAME,
        metadata={"hnsw:space": "cosine"},
    )

    ids = data["ids"]
    documents = data["documents"]
    metadatas = data["metadatas"]
    embeddings = data["embeddings"]

    for i in range(0, total, BATCH_SIZE):
        end = min(i + BATCH_SIZE, total)
        new_collection.add(
            ids=ids[i:end],
            documents=documents[i:end],
            metadatas=metadatas[i:end],
            embeddings=embeddings[i:end],
        )
        print(f"  {end}/{total} migrados...")

    # Swap: borramos la vieja y renombramos la nueva al nombre original.
    # ChromaDB permite renombrar colecciones directamente.
    client.delete_collection(COLLECTION_NAME)
    new_collection.modify(name=COLLECTION_NAME)

    print(f"\nListo. '{COLLECTION_NAME}' ahora usa métrica coseno, con los {total} "
          f"chunks originales intactos (mismos embeddings, sin re-llamar a Ollama).")


if __name__ == "__main__":
    migrate()
