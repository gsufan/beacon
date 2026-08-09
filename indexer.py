"""
Indexer — une el chunker políglota (4A), la detección incremental vía git (4B)
y el almacenamiento vectorial local (4C: ChromaDB + embeddings de Ollama).

Flujo por cada ejecución:
  1. Lee el hash del último commit indexado (guardado en una colección de
     control dentro de la misma base ChromaDB, así todo el estado vive en
     un solo lugar y no depende de un archivo externo que se pueda perder).
  2. Calcula el diff contra HEAD con GitWatcher.
  3. Para archivos eliminados/renombrados(old): purga sus chunks viejos.
  4. Para archivos añadidos/modificados/renombrados(new):
       - si es modificado o renombrado: purga primero sus chunks viejos
         (por si cambió la cantidad/rango de funciones)
       - chunkea con el chunker políglota
       - genera embeddings locales con Ollama (nomic-embed-text)
       - inserta en ChromaDB con chunk_id() determinístico
  5. Guarda el nuevo HEAD como "último commit indexado".
"""

import os
from typing import List

import chromadb
import ollama
from dotenv import load_dotenv

from chunker import chunk_file, CodeChunk
from git_watcher import GitWatcher, ChangeSet

load_dotenv()

EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "nomic-embed-text")
CHROMA_DIR = os.getenv("CHROMA_PERSIST_DIR", "./data/chroma_db")
COLLECTION_NAME = os.getenv("CHROMA_COLLECTION_NAME", "codebase_index")

# Colección aparte, chica, solo para guardar el estado de indexación
# (qué commit fue el último procesado). Vive en la misma base ChromaDB.
CONTROL_COLLECTION_NAME = "sys_index_control"
CONTROL_KEY_ID = "last_indexed_commit"


class CodebaseIndexer:
    def __init__(self, repo_path: str):
        self.repo_path = repo_path
        self.watcher = GitWatcher(repo_path)
        self.client = chromadb.PersistentClient(path=CHROMA_DIR)
        # ChromaDB usa L2 por defecto, que no es la métrica recomendada para
        # embeddings de texto (nomic-embed-text espera similitud coseno).
        # Se especifica explícitamente al crear la colección, porque la
        # métrica NO se puede cambiar después sin recrearla.
        self.collection = self.client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
        self.control = self.client.get_or_create_collection(name=CONTROL_COLLECTION_NAME)

    # ---------- Estado de indexación ----------

    def _get_last_indexed_commit(self):
        result = self.control.get(ids=[CONTROL_KEY_ID])
        if result["ids"]:
            return result["metadatas"][0]["commit_hash"]
        return None

    def _save_last_indexed_commit(self, commit_hash: str):
        # Esta colección no se usa para búsqueda semántica, solo como key-value
        # store del último commit indexado. Le pasamos un embedding dummy
        # explícito para que ChromaDB NUNCA intente descargar su modelo de
        # embeddings por defecto desde internet (rompería el pilar de
        # "costo $0 / 100% local" de la propuesta).
        self.control.upsert(
            ids=[CONTROL_KEY_ID],
            documents=[commit_hash],
            embeddings=[[0.0]],
            metadatas=[{"commit_hash": commit_hash}],
        )

    # ---------- Embeddings ----------

    # nomic-embed-text soporta hasta 8192 tokens, pero Ollama usa 2048 por
    # defecto si no se especifica. Lo subimos explícitamente para evitar
    # "input length exceeds the context length" en chunks grandes (clases
    # largas, archivos generados como protobuf/gRPC, etc.)
    EMBED_NUM_CTX = 8192

    # Si el chunk es obviamente enorme, ni siquiera intentamos embeberlo
    # entero (ahorra una llamada que sabemos que va a fallar). Chunks más
    # chicos que esto SÍ se intentan directo, sin dividir de entrada.
    DIRECT_TRY_MAX_CHARS = 16000

    # Piso de la división recursiva: si tras partir a la mitad varias veces
    # seguimos sin poder embeber, nos rendimos con esta pieza en vez de
    # seguir dividiendo indefinidamente.
    MIN_SPLIT_CHARS = 800
    MAX_SPLIT_DEPTH = 8

    def _embed(self, text: str) -> List[float]:
        # nomic-embed-text requiere un prefijo de tarea explícito: sin él,
        # la calidad de recuperación se degrada notablemente (documentado
        # oficialmente por Nomic). Todo lo que se INDEXA es "documento".
        response = ollama.embeddings(
            model=EMBEDDING_MODEL, prompt=f"search_document: {text}",
            options={"num_ctx": self.EMBED_NUM_CTX},
        )
        return response["embedding"]

    def _embed_adaptive(self, chunk_id: str, code: str, base_meta: dict,
                         out: List[tuple], depth: int = 0) -> None:
        """
        Intenta embeber 'code' completo. Si el modelo lo rechaza por exceder
        el contexto, lo divide a la mitad (respetando saltos de línea) y
        reintenta cada mitad recursivamente. Esto se adapta al límite real
        del modelo en vez de depender de un umbral fijo adivinado, que para
        código denso en símbolos (generado, minificado) puede no alcanzar.
        Va acumulando (id, code, meta, embedding) en 'out'.
        """
        if depth == 0 and len(code) > self.DIRECT_TRY_MAX_CHARS:
            # Obviamente gigante: nos ahorramos el intento directo que sabemos
            # que va a fallar, y vamos derecho a dividir.
            self._split_and_recurse(chunk_id, code, base_meta, out, depth)
            return

        try:
            embedding = self._embed(code)
            out.append((chunk_id, code, base_meta, embedding))
            return
        except ollama.ResponseError:
            pass

        if len(code) <= self.MIN_SPLIT_CHARS or depth >= self.MAX_SPLIT_DEPTH:
            print(f"  [WARN] No se pudo embeber {chunk_id} ni tras dividir "
                  f"({len(code)} chars, profundidad {depth}). Se omite esta pieza.")
            return

        self._split_and_recurse(chunk_id, code, base_meta, out, depth)

    def _split_and_recurse(self, chunk_id: str, code: str, base_meta: dict,
                            out: List[tuple], depth: int) -> None:
        mid = len(code) // 2
        # Cortamos en el salto de línea más cercano al punto medio para no
        # partir una línea de código a la mitad.
        split_at = code.rfind("\n", 0, mid)
        if split_at <= 0:
            split_at = mid

        left_meta = dict(base_meta, split_depth=depth + 1, split_of=chunk_id)
        right_meta = dict(base_meta, split_depth=depth + 1, split_of=chunk_id)

        self._embed_adaptive(f"{chunk_id}::L{depth + 1}", code[:split_at], left_meta, out, depth + 1)
        self._embed_adaptive(f"{chunk_id}::R{depth + 1}", code[split_at:], right_meta, out, depth + 1)

    # ---------- Purga ----------

    def _purge_file(self, file_path: str) -> int:
        """Elimina todos los chunks indexados de un archivo. Devuelve cuántos borró."""
        existing = self.collection.get(where={"file_path": file_path})
        if existing["ids"]:
            self.collection.delete(ids=existing["ids"])
        return len(existing["ids"])

    # ---------- Indexación de un archivo ----------

    def _index_file(self, file_path: str) -> int:
        """Chunkea, embebe e inserta un archivo. Devuelve cuántos chunks insertó."""
        full_path = os.path.join(self.repo_path, file_path)
        if not os.path.exists(full_path):
            return 0

        chunks: List[CodeChunk] = chunk_file(full_path)
        if not chunks:
            return 0

        ids, documents, embeddings, metadatas = [], [], [], []
        for chunk in chunks:
            # Guardamos file_path RELATIVO al repo (no la ruta absoluta local),
            # así el índice es portable y coincide con lo que devuelve git diff.
            chunk.file_path = file_path

            results: List[tuple] = []
            self._embed_adaptive(chunk.chunk_id(), chunk.code, chunk.to_metadata(), results)

            for piece_id, piece_code, piece_meta, embedding in results:
                ids.append(piece_id)
                documents.append(piece_code)
                embeddings.append(embedding)
                metadatas.append(piece_meta)

        if not ids:
            return 0

        self.collection.upsert(
            ids=ids, documents=documents, embeddings=embeddings, metadatas=metadatas,
        )
        return len(ids)

    # ---------- Orquestación principal ----------

    def sync(self, include_uncommitted: bool = False) -> dict:
        """
        Ejecuta un ciclo completo de indexación incremental.
        Devuelve un resumen con lo que se hizo, para logging/reporte.
        """
        last_commit = self._get_last_indexed_commit()
        changes: ChangeSet = self.watcher.get_changes_since(last_commit)

        if include_uncommitted:
            uncommitted = self.watcher.get_uncommitted_changes()
            changes.added += uncommitted.added
            changes.modified += uncommitted.modified
            changes.deleted += uncommitted.deleted

        if changes.is_empty():
            return {"status": "sin_cambios", "detalle": changes.summary()}

        purged_count = 0
        for file_path in changes.files_to_purge():
            purged_count += self._purge_file(file_path)

        # Modificados y renombrados(new) también se purgan antes de re-indexar,
        # por si la cantidad de chunks cambió (ej: se borró un método).
        for file_path in changes.modified:
            purged_count += self._purge_file(file_path)

        indexed_count = 0
        files_indexed = 0
        for file_path in changes.files_to_reindex():
            n = self._index_file(file_path)
            if n > 0:
                indexed_count += n
                files_indexed += 1

        # Solo avanzamos el puntero de "último commit indexado" si NO estamos
        # incluyendo cambios sin commitear (esos no tienen un hash estable al
        # cual apuntar; se re-evaluarán en la próxima sync de todos modos).
        if not include_uncommitted:
            self._save_last_indexed_commit(self.watcher.current_commit_hash())

        return {
            "status": "ok",
            "detalle": changes.summary(),
            "archivos_indexados": files_indexed,
            "chunks_insertados": indexed_count,
            "chunks_purgados": purged_count,
        }

    def stats(self) -> dict:
        return {
            "total_chunks": self.collection.count(),
            "ultimo_commit_indexado": self._get_last_indexed_commit(),
        }


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Uso: python indexer.py <ruta_repo> [--uncommitted]")
        sys.exit(1)

    repo_path = sys.argv[1]
    include_uncommitted = "--uncommitted" in sys.argv

    indexer = CodebaseIndexer(repo_path)
    print("Sincronizando índice...")
    result = indexer.sync(include_uncommitted=include_uncommitted)
    print(result)
    print("\nEstadísticas actuales:")
    print(indexer.stats())
