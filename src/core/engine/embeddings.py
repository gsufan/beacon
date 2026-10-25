"""Embeddings: per-model format, real context size and batching.

Each model expects a different format for queries and documents, and using the wrong one degrades
retrieval. This module keeps that knowledge in one place so switching models is a one-line change in
config.yaml. The default, qwen3-embedding:0.6b, is multilingual and small enough to fit next to
llama3:8b on an 8 GB GPU (see docs/en/measured-quality.md).
"""

from dataclasses import dataclass
from typing import List

import ollama


_QWEN_TASK = "Given a developer question, retrieve the source code that implements the answer"


@dataclass(frozen=True)
class EmbeddingProfile:
    query_prefix: str
    document_prefix: str
    num_ctx: int  # window requested from Ollama (no larger than the model's real one)


# Key: model name prefix in Ollama (without the tag).
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


def _embed_batch(client, model: str, texts: List[str], num_ctx: int) -> List[List[float]]:
    # truncate=False: if a text does not fit, Ollama returns an error (instead of silently cutting it)
    # and the indexer splits it into parts.
    response = client.embed(model=model, input=texts, truncate=False, options={"num_ctx": num_ctx})
    vectors = response["embeddings"]
    if len(vectors) != len(texts) or any(not v for v in vectors):
        raise ollama.ResponseError("el modelo devolvió embeddings vacíos", 500)
    return vectors


def embed_documents(client, model: str, texts: List[str]) -> List[List[float]]:
    p = profile_for(model)
    return _embed_batch(client, model, [p.document_prefix + t for t in texts], p.num_ctx)


def embed_query(client, model: str, text: str) -> List[float]:
    p = profile_for(model)
    return _embed_batch(client, model, [p.query_prefix + text], p.num_ctx)[0]
