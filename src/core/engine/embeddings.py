"""Embeddings: per-model format, real context size and batching.

Each model expects a different format for queries and documents, and using the wrong one degrades
retrieval. This module keeps that knowledge in one place so switching models is a one-line change in
config.yaml. The default, qwen3-embedding:0.6b, is multilingual and small enough to fit next to
llama3:8b on an 8 GB GPU (see docs/en/measured-quality.md).
"""

from dataclasses import dataclass
from typing import List

from core.engine.backends import Backend


_QWEN_TASK = "Given a developer question, retrieve the source code that implements the answer"


@dataclass(frozen=True)
class EmbeddingProfile:
    query_prefix: str
    document_prefix: str
    num_ctx: int  # window requested from the server (no larger than the model's real one)


# Key: model name prefix (without the tag).
_PROFILES = {
    "qwen3-embedding": EmbeddingProfile(f"Instruct: {_QWEN_TASK}\nQuery:", "", 8192),
    "nomic-embed-text": EmbeddingProfile("search_query: ", "search_document: ", 2048),  # 2,048 real
    "embeddinggemma": EmbeddingProfile("task: code retrieval | query: ", "title: none | text: ", 2048),
    "bge-m3": EmbeddingProfile("", "", 8192),
}
_GENERIC = EmbeddingProfile("", "", 2048)


def profile_for(model: str) -> EmbeddingProfile:
    base = model.split(":", 1)[0]
    return _PROFILES.get(base, _GENERIC)


def embed_documents(backend: Backend, model: str, texts: List[str]) -> List[List[float]]:
    p = profile_for(model)
    return backend.embed(model, [p.document_prefix + t for t in texts], num_ctx=p.num_ctx)


def embed_query(backend: Backend, model: str, text: str) -> List[float]:
    p = profile_for(model)
    return backend.embed(model, [p.query_prefix + text], num_ctx=p.num_ctx)[0]
