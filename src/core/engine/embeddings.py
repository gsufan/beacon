"""Embeddings: formato por modelo, contexto real y cálculo por lotes.

Cada modelo de embeddings espera un formato distinto para las consultas y los
documentos, y usar el equivocado degrada mucho la recuperación (con
nomic-embed-text, omitir "search_query: "/"search_document: " fue un bug real).
Este módulo concentra ese conocimiento para que cambiar de modelo sea cambiar
una línea de config.yaml.

Modelo por defecto: qwen3-embedding:0.6b. Se eligió midiendo con
tools/eval_retrieval.py sobre psf/requests (30 preguntas en español sobre
código en inglés), con la misma penalización de tests en todos:

    modelo                  Hit@1  Hit@5  Hit@10  MRR@10
    nomic-embed-text         27%    50%    63%    0,373
    bge-m3                   37%    90%    93%    0,599
    embeddinggemma           70%    90%    93%    0,794
    qwen3-embedding:0.6b     70%    93%   100%    0,796

nomic-embed-text está entrenado casi solo en inglés; con preguntas en español
fallaba justamente en lo que Beacon necesita. qwen3-embedding es multilingüe y
cabe junto a llama3:8b en una GPU de 8 GB (~0,6 GB).
"""

from dataclasses import dataclass
from typing import List

import ollama


_QWEN_TASK = "Given a developer question, retrieve the source code that implements the answer"


@dataclass(frozen=True)
class EmbeddingProfile:
    query_prefix: str
    document_prefix: str
    num_ctx: int  # ventana que se pide a Ollama (no más que la real del modelo)


# Clave: prefijo del nombre del modelo en Ollama (sin tag).
_PROFILES = {
    "qwen3-embedding": EmbeddingProfile(f"Instruct: {_QWEN_TASK}\nQuery:", "", 8192),
    "nomic-embed-text": EmbeddingProfile("search_query: ", "search_document: ", 2048),  # 2.048 reales
    "embeddinggemma": EmbeddingProfile("task: code retrieval | query: ", "title: none | text: ", 2048),
    "bge-m3": EmbeddingProfile("", "", 8192),
}
_GENERIC = EmbeddingProfile("", "", 2048)


def profile_for(model: str) -> EmbeddingProfile:
    base = model.split(":", 1)[0]
    return _PROFILES.get(base, _GENERIC)


def _embed_batch(client, model: str, texts: List[str], num_ctx: int) -> List[List[float]]:
    # truncate=False: si un texto no cabe, Ollama responde error (en vez de
    # recortarlo en silencio) y el indexador lo divide en partes.
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
