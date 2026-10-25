"""Incremental indexer: chunker, git diff, ChromaDB and embeddings."""

import os
from pathlib import Path
from typing import Callable, List, Optional

from core.engine.chroma_utils import bump_index_version, get_chroma_client, remove_orphan_segments
from core.engine.backends import InputRejectedError

from core.config import AIProviderConfig
from core.projects import ProjectContext
from core.engine.chunker import chunk_file, chunk_source, CodeChunk
from core.engine.embeddings import embed_documents
from core.engine.git_watcher import GitWatcher, ChangeSet

COLLECTION_NAME = "codebase_index"
STAGING_COLLECTION_NAME = "codebase_index__rebuild"
CONTROL_COLLECTION_NAME = "sys_index_control"
CONTROL_KEY_ID = "last_indexed_commit"
LEGACY_EMBEDDING_MODEL = "nomic-embed-text"
# Version of what is stored in the index (what is embedded and how files are split). If it changes,
# the next sync rebuilds the whole index so fragments of both versions are not mixed.
#   1: code only.
#   2: file path before the code, without @overload signatures.
INDEX_FORMAT = 2


class IndexModelMismatchError(RuntimeError):
    """The index was built with another embedding model: its vectors are not comparable with query vectors and it must be reindexed."""

def embedding_text(meta: dict, code: str) -> str:
    """Text embedded for a chunk: the code preceded by its path and name. The code alone does not say which
    module or service it belongs to (in a microservices repository two `getQuote` functions look alike);
    the path does. What is stored and given to the model is still only the code.
    """
    return f"{meta.get('file_path', '')}\n\n{code}"


ProgressCallback = Optional[Callable[[int, int, str], None]]


class CodebaseIndexer:
    def __init__(self, project: ProjectContext, ai_config: AIProviderConfig):
        self.project = project
        self.ai_config = ai_config
        self.watcher = GitWatcher(project.repo_path)
        self.backend = ai_config.backend(Path(project.chroma_dir).parent)
        self.client = get_chroma_client(project.chroma_dir)
        self.collection = self.client.get_or_create_collection(
            name=COLLECTION_NAME, metadata={"hnsw:space": "cosine"}
        )
        self.control = self.client.get_or_create_collection(name=CONTROL_COLLECTION_NAME)


    def _get_last_indexed_commit(self):
        result = self.control.get(ids=[CONTROL_KEY_ID])
        return result["metadatas"][0]["commit_hash"] if result["ids"] else None

    def indexed_embedding_model(self) -> Optional[str]:
        return indexed_embedding_model(self.control, self.collection)

    def _indexed_format(self) -> int:
        result = self.control.get(ids=[CONTROL_KEY_ID])
        return result["metadatas"][0].get("index_format", 1) if result["ids"] else 1

    def _save_last_indexed_commit(self, commit_hash: str):
        self.control.upsert(
            ids=[CONTROL_KEY_ID], documents=[commit_hash], embeddings=[[0.0]],
            metadatas=[{"commit_hash": commit_hash, "embedding_model": self.ai_config.embedding_model,
                        "index_format": INDEX_FORMAT}],
        )

    def _start_rebuild(self):
        """Starts a full rebuild in a separate collection; the current one keeps answering queries until
        the swap. It also serves a model change, since the new collection accepts another vector dimension.
        """
        try:
            self.client.delete_collection(STAGING_COLLECTION_NAME)  # leftovers from a failed attempt
        except Exception:
            pass
        live = self.collection
        self.collection = self.client.create_collection(name=STAGING_COLLECTION_NAME,
                                                        metadata={"hnsw:space": "cosine"})
        return live

    def _finish_rebuild(self):
        self.client.delete_collection(COLLECTION_NAME)
        self.collection.modify(name=COLLECTION_NAME)
        self.collection = self.client.get_collection(COLLECTION_NAME)

    def _abort_rebuild(self, live):
        try:
            self.client.delete_collection(STAGING_COLLECTION_NAME)
        except Exception:
            pass
        self.collection = live


    DIRECT_TRY_MAX_CHARS = 16000
    MIN_SPLIT_CHARS = 800
    MAX_SPLIT_DEPTH = 8

    def _embed(self, text: str) -> List[float]:
        return embed_documents(self.backend, self.ai_config.embedding_model, [text])[0]

    def _embed_adaptive(self, chunk_id, code, base_meta, out, depth=0):
        if depth == 0 and len(code) > self.DIRECT_TRY_MAX_CHARS:
            self._split_and_recurse(chunk_id, code, base_meta, out, depth)
            return
        try:
            out.append((chunk_id, code, base_meta, self._embed(embedding_text(base_meta, code))))
            return
        except InputRejectedError:
            # Only "text too long" justifies splitting; a missing model or a server that is down must propagate,
            # or the sync would mark the commit as indexed with 0 chunks.
            pass
        if len(code) <= self.MIN_SPLIT_CHARS or depth >= self.MAX_SPLIT_DEPTH:
            return  # skipped: it could not be embedded even after splitting
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

    def _embed_file_chunks(self, chunks: List[CodeChunk]) -> list:
        """Embeddings for all chunks of a file in one batched call. If one does not fit in the model, it
        falls back to chunk-by-chunk, which splits long chunks recursively.
        """
        results = []
        if all(len(c.code) <= self.DIRECT_TRY_MAX_CHARS for c in chunks):
            try:
                texts = [embedding_text(c.to_metadata(), c.code) for c in chunks]
                vectors = embed_documents(self.backend, self.ai_config.embedding_model, texts)
                return [(c.chunk_id(), c.code, c.to_metadata(), v) for c, v in zip(chunks, vectors)]
            except InputRejectedError:
                pass  # a missing model or a server that is down is not a size problem and propagates
        for c in chunks:
            self._embed_adaptive(c.chunk_id(), c.code, c.to_metadata(), results)
        return results


    def _purge_file(self, file_path: str) -> int:
        existing = self.collection.get(where={"file_path": file_path})
        if existing["ids"]:
            self.collection.delete(ids=existing["ids"])
        return len(existing["ids"])

    def _index_file(self, file_path: str, commit_hash: Optional[str] = None) -> int:
        """Indexes one file. With `commit_hash` the content is read from that commit (the normal case);
        without it, from disk (only the --uncommitted mode, which records no commit).
        """
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

        for chunk in chunks:
            chunk.file_path = file_path
        results = self._embed_file_chunks(chunks)
        ids = [r[0] for r in results]
        documents = [r[1] for r in results]
        metadatas = [r[2] for r in results]
        embeddings = [r[3] for r in results]

        if not ids:
            return 0
        self.collection.upsert(ids=ids, documents=documents, embeddings=embeddings, metadatas=metadatas)
        return len(ids)


    def sync(self, include_uncommitted: bool = False, on_progress: ProgressCallback = None,
             full: bool = False) -> dict:
        remove_orphan_segments(self.project.chroma_dir)
        previous_model = self.indexed_embedding_model()
        model_changed = previous_model is not None and previous_model != self.ai_config.embedding_model
        if model_changed:
            full = True  # another model's vectors are not comparable: rebuild everything
        previous_format = self._indexed_format()
        format_changed = previous_model is not None and not model_changed and previous_format != INDEX_FORMAT
        if format_changed:
            full = True  # same model and dimension: rebuilding is enough
        last_commit = None if full else self._get_last_indexed_commit()
        # The commit is fixed at the start: the diff, the indexed content and the recorded hash all belong to
        # the same commit even if another arrives during the sync.
        target = self.watcher.current_commit_hash()
        changes: ChangeSet = self.watcher.get_changes_since(last_commit, target=target)

        if include_uncommitted:
            u = self.watcher.get_uncommitted_changes()
            changes.added += u.added
            changes.modified += u.modified
            changes.deleted += u.deleted

        if changes.is_empty() and not changes.full_rescan:
            return {"status": "sin_cambios", "detalle": changes.summary()}

        files = changes.files_to_reindex()
        purged = 0
        indexed, files_indexed = 0, 0
        # A full rebuild is assembled in a new collection instead of deleting chunk by chunk: hnswlib only marks
        # deletions, so the index grows and, with many deleted elements, search can fail.
        live = self._start_rebuild() if changes.full_rescan else None
        try:
            if changes.full_rescan:
                purged += live.count()
            else:
                # also those being reindexed: an 'added' file may have been indexed by a previous --uncommitted run
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
            if live is not None:
                self._finish_rebuild()
                live = None
        finally:
            if live is not None:  # failed midway: the previous index stays intact
                self._abort_rebuild(live)
            # Even if the sync fails midway the index has changed: notify other processes (the server) so they
            # stop searching their stale copy.
            bump_index_version(os.path.dirname(self.project.chroma_dir))

        if not include_uncommitted:
            self._save_last_indexed_commit(target)

        return {
            "status": "ok", "detalle": changes.summary(),
            "archivos_indexados": files_indexed, "chunks_insertados": indexed, "chunks_purgados": purged,
            **({"modelo_embeddings": f"{previous_model} → {self.ai_config.embedding_model} (índice reconstruido)"}
               if model_changed else {}),
            **({"formato_indice": f"{previous_format} → {INDEX_FORMAT} (índice reconstruido)"}
               if format_changed else {}),
        }

    def stats(self) -> dict:
        return {
            "total_chunks": self.collection.count(),
            "ultimo_commit_indexado": self._get_last_indexed_commit(),
            "commit_actual": self.watcher.current_commit_hash(),
        }


def indexed_embedding_model(control, collection) -> Optional[str]:
    """Model recorded in the control collection; indexes created before it was recorded used LEGACY_EMBEDDING_MODEL."""
    result = control.get(ids=[CONTROL_KEY_ID])
    if result["ids"]:
        return result["metadatas"][0].get("embedding_model") or LEGACY_EMBEDDING_MODEL
    return LEGACY_EMBEDDING_MODEL if collection.count() else None
