"""Indexador incremental: chunker + git diff + ChromaDB + embeddings."""

import os
from typing import Callable, List, Optional

from core.engine.chroma_utils import bump_index_version, get_chroma_client
import ollama

from core.config import AIProviderConfig
from core.projects import ProjectContext
from core.engine.chunker import chunk_file, chunk_source, CodeChunk
from core.engine.git_watcher import GitWatcher, ChangeSet

COLLECTION_NAME = "codebase_index"
CONTROL_COLLECTION_NAME = "sys_index_control"
CONTROL_KEY_ID = "last_indexed_commit"

# Progreso: callback(current, total, file_path) — la CLI/UI lo usan para
# mostrar avance real en vez de una consola muda.
ProgressCallback = Optional[Callable[[int, int, str], None]]


class CodebaseIndexer:
    def __init__(self, project: ProjectContext, ai_config: AIProviderConfig):
        self.project = project
        self.ai_config = ai_config
        self.watcher = GitWatcher(project.repo_path)
        self.client_ollama = ollama.Client(host=ai_config.ollama_host)
        self.client = get_chroma_client(project.chroma_dir)
        self.collection = self.client.get_or_create_collection(
            name=COLLECTION_NAME, metadata={"hnsw:space": "cosine"}
        )
        self.control = self.client.get_or_create_collection(name=CONTROL_COLLECTION_NAME)

    # ---------- Estado ----------

    def _get_last_indexed_commit(self):
        result = self.control.get(ids=[CONTROL_KEY_ID])
        return result["metadatas"][0]["commit_hash"] if result["ids"] else None

    def _save_last_indexed_commit(self, commit_hash: str):
        self.control.upsert(
            ids=[CONTROL_KEY_ID], documents=[commit_hash],
            embeddings=[[0.0]], metadatas=[{"commit_hash": commit_hash}],
        )

    # ---------- Embeddings (con división recursiva adaptativa) ----------

    EMBED_NUM_CTX = 8192
    DIRECT_TRY_MAX_CHARS = 16000
    MIN_SPLIT_CHARS = 800
    MAX_SPLIT_DEPTH = 8

    def _embed(self, text: str) -> List[float]:
        response = self.client_ollama.embeddings(
            model=self.ai_config.embedding_model,
            prompt=f"search_document: {text}",
            options={"num_ctx": self.EMBED_NUM_CTX},
        )
        return response["embedding"]

    def _embed_adaptive(self, chunk_id, code, base_meta, out, depth=0):
        if depth == 0 and len(code) > self.DIRECT_TRY_MAX_CHARS:
            self._split_and_recurse(chunk_id, code, base_meta, out, depth)
            return
        try:
            out.append((chunk_id, code, base_meta, self._embed(code)))
            return
        except ollama.ResponseError as e:
            # Solo "texto demasiado largo" amerita dividir; modelo faltante u Ollama caído
            # deben propagarse, o el sync marcaría el commit como indexado con 0 chunks.
            if e.status_code == 404:
                raise
        if len(code) <= self.MIN_SPLIT_CHARS or depth >= self.MAX_SPLIT_DEPTH:
            return  # se omite: no se pudo embeber ni dividiendo
        self._split_and_recurse(chunk_id, code, base_meta, out, depth)

    def _split_and_recurse(self, chunk_id, code, base_meta, out, depth):
        mid = len(code) // 2
        split_at = code.rfind("\n", 0, mid)
        if split_at <= 0:
            split_at = mid
        left_meta = dict(base_meta, split_depth=depth + 1, split_of=chunk_id)
        right_meta = dict(base_meta, split_depth=depth + 1, split_of=chunk_id)
        self._embed_adaptive(f"{chunk_id}::L{depth+1}", code[:split_at], left_meta, out, depth + 1)
        self._embed_adaptive(f"{chunk_id}::R{depth+1}", code[split_at:], right_meta, out, depth + 1)

    # ---------- Purga / indexación de un archivo ----------

    def _purge_file(self, file_path: str) -> int:
        existing = self.collection.get(where={"file_path": file_path})
        if existing["ids"]:
            self.collection.delete(ids=existing["ids"])
        return len(existing["ids"])

    def _purge_all(self) -> int:
        ids = self.collection.get(include=[])["ids"]
        for i in range(0, len(ids), 5000):  # por tandas: SQLite limita las variables por consulta
            self.collection.delete(ids=ids[i:i + 5000])
        return len(ids)

    def _index_file(self, file_path: str, commit_hash: Optional[str] = None) -> int:
        """Indexa un archivo. Con `commit_hash`, el contenido se lee desde ese
        commit (el caso normal); sin él, desde el disco (solo el modo
        --uncommitted, que por diseño no registra ningún commit)."""
        if commit_hash:
            source = self.watcher.read_file_at(commit_hash, file_path)
            if source is None:
                return 0
            chunks = chunk_source(file_path, source)
        else:
            full_path = os.path.join(self.project.repo_path, file_path)
            if not os.path.exists(full_path):
                return 0
            chunks = chunk_file(full_path)
        if not chunks:
            return 0

        ids, documents, embeddings, metadatas = [], [], [], []
        for chunk in chunks:
            chunk.file_path = file_path
            results = []
            self._embed_adaptive(chunk.chunk_id(), chunk.code, chunk.to_metadata(), results)
            for piece_id, piece_code, piece_meta, embedding in results:
                ids.append(piece_id)
                documents.append(piece_code)
                embeddings.append(embedding)
                metadatas.append(piece_meta)

        if not ids:
            return 0
        self.collection.upsert(ids=ids, documents=documents, embeddings=embeddings, metadatas=metadatas)
        return len(ids)

    # ---------- Orquestación ----------

    def sync(self, include_uncommitted: bool = False, on_progress: ProgressCallback = None,
             full: bool = False) -> dict:
        # full=True: rehace el índice entero (ej. tras actualizar el chunker)
        last_commit = None if full else self._get_last_indexed_commit()
        # Se fija el commit al empezar: el diff, el contenido indexado y el hash
        # registrado al final son del mismo commit aunque llegue otro durante
        # el sync (antes se leía HEAD al final y el disco durante el proceso).
        target = self.watcher.current_commit_hash()
        changes: ChangeSet = self.watcher.get_changes_since(last_commit, target=target)

        if include_uncommitted:
            u = self.watcher.get_uncommitted_changes()
            changes.added += u.added
            changes.modified += u.modified
            changes.deleted += u.deleted

        if changes.is_empty():
            return {"status": "sin_cambios", "detalle": changes.summary()}

        files = changes.files_to_reindex()
        purged = 0
        indexed, files_indexed = 0, 0
        try:
            if changes.full_rescan:
                purged += self._purge_all()
            else:
                # también los que se reindexan: un 'added' pudo quedar indexado por un --uncommitted previo
                for fp in changes.files_to_purge() + files:
                    purged += self._purge_file(fp)
            total = len(files)
            for i, fp in enumerate(files, 1):
                n = self._index_file(fp, commit_hash=None if include_uncommitted else target)
                if n > 0:
                    indexed += n
                    files_indexed += 1
                if on_progress:
                    on_progress(i, total, fp)
        finally:
            # Aunque el sync falle a mitad, el índice ya cambió: avisar a otros
            # procesos (el servidor) para que no sigan buscando en su copia vieja.
            bump_index_version(os.path.dirname(self.project.chroma_dir))

        if not include_uncommitted:
            self._save_last_indexed_commit(target)

        return {
            "status": "ok", "detalle": changes.summary(),
            "archivos_indexados": files_indexed, "chunks_insertados": indexed, "chunks_purgados": purged,
        }

    def stats(self) -> dict:
        return {
            "total_chunks": self.collection.count(),
            "ultimo_commit_indexado": self._get_last_indexed_commit(),
            "commit_actual": self.watcher.current_commit_hash(),
        }
