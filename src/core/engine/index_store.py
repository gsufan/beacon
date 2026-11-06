"""Collections of a project's index in ChromaDB: the code chunks and the control record kept beside them."""

from typing import Optional

COLLECTION_NAME = "codebase_index"
STAGING_COLLECTION_NAME = "codebase_index__rebuild"
CONTROL_COLLECTION_NAME = "sys_index_control"
CODE_COLLECTION_METADATA = {"hnsw:space": "cosine"}
# Indexes created before the embedding model was recorded used this one.
LEGACY_EMBEDDING_MODEL = "nomic-embed-text"

_INDEXED_KEY = "last_indexed_commit"
_DOCUMENTED_KEY = "last_documented_commit"


def open_code_collection(client, name: str = COLLECTION_NAME):
    """Looked up by name on each call: a full rebuild replaces the collection, and an old reference points to the deleted one."""
    return client.get_or_create_collection(name=name, metadata=CODE_COLLECTION_METADATA)


def create_code_collection(client, name: str):
    return client.create_collection(name=name, metadata=CODE_COLLECTION_METADATA)


class IndexControl:
    """Last indexed and documented commits, embedding model and index format, stored in the control collection."""

    def __init__(self, client):
        self.collection = client.get_or_create_collection(name=CONTROL_COLLECTION_NAME)

    def _metadata(self, key: str) -> Optional[dict]:
        result = self.collection.get(ids=[key])
        return result["metadatas"][0] if result["ids"] else None

    def _save(self, key: str, metadata: dict):
        self.collection.upsert(ids=[key], documents=[metadata["commit_hash"]], embeddings=[[0.0]], metadatas=[metadata])

    def last_indexed_commit(self) -> Optional[str]:
        meta = self._metadata(_INDEXED_KEY)
        return meta["commit_hash"] if meta else None

    def last_documented_commit(self) -> Optional[str]:
        meta = self._metadata(_DOCUMENTED_KEY)
        return meta["commit_hash"] if meta else None

    def index_format(self) -> int:
        meta = self._metadata(_INDEXED_KEY)
        return meta.get("index_format", 1) if meta else 1

    def embedding_model(self, code_collection) -> Optional[str]:
        """Model the index was built with; None if there is no index yet."""
        meta = self._metadata(_INDEXED_KEY)
        if meta:
            return meta.get("embedding_model") or LEGACY_EMBEDDING_MODEL
        return LEGACY_EMBEDDING_MODEL if code_collection.count() else None

    def save_indexed(self, commit_hash: str, embedding_model: str, index_format: int):
        self._save(_INDEXED_KEY, {"commit_hash": commit_hash, "embedding_model": embedding_model, "index_format": index_format})

    def save_documented(self, commit_hash: str):
        self._save(_DOCUMENTED_KEY, {"commit_hash": commit_hash})
