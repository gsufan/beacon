"""Retrieval: semantic search in the index, combined with identifier matches and reranked."""

import logging
import re
from dataclasses import dataclass
from typing import Callable, List

import numpy as np
from chromadb.errors import InvalidCollectionException

from core.engine.index_store import open_code_collection

logger = logging.getLogger("beacon.rag")

DEFAULT_TOP_K = 5

# Test names repeat the words of the question and outranked the function that answers it: this
# distance is added to them unless the question is about tests.
TEST_DISTANCE_PENALTY = 0.08
TEST_PATH_PATTERN = re.compile(r"(^|/)(tests?|testing|__tests__)/|(^|/)test_[^/]*$|_test\.[a-z]+$|\.(test|spec)\.[jt]sx?$")
ASKS_ABOUT_TESTS = re.compile(r"\b(tests?|testing|prueba|pruebas|testear|spec)\b", re.IGNORECASE)

# Adaptive context: besides top_k, chunks almost as relevant as the last one chosen (within this
# distance margin) are added, up to this maximum. The token budget remains the final cap.
ADAPTIVE_MARGIN = 0.03
ADAPTIVE_MAX_EXTRA = 5

# Hybrid search: when the question names a code identifier (`super_len`, `TooManyRedirects`), chunks
# named like it or using it are added as candidates, even if semantic search did not return them, and
# get a distance discount.
NAME_MATCH_BONUS = 0.10
CODE_MATCH_BONUS = 0.04
# An identifier present in more chunks than this is too common to guide the search (e.g. `self`,
# `request`) and its occurrences in code are ignored.
IDENTIFIER_MAX_CODE_MATCHES = 15
QUESTION_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]{3,}")
# "Where is X used?", "who calls X?": the question is about whoever uses the identifier, not its
# definition, so the larger discount goes to the chunks that use it.
ASKS_ABOUT_USAGE = re.compile(
    r"\b(d[oó]nde|qui[eé]n|qu[eé]|cu[aá]ndo)\b.{0,25}\b(usa|usan|utiliza|llama|llaman|invoca|lanza|lanzan|"
    r"levanta|produce|lee|leen|modifica|asigna)\b", re.IGNORECASE)

_INCLUDE_WITH_VECTORS = ["documents", "metadatas", "embeddings"]


@dataclass
class RetrievedChunk:
    file_path: str
    chunk_type: str
    name: str
    start_line: int
    end_line: int
    code: str
    distance: float
    expanded: bool = False

    @classmethod
    def from_index(cls, code: str, meta: dict, distance: float, expanded: bool = False) -> "RetrievedChunk":
        return cls(file_path=meta.get("file_path", "?"), chunk_type=meta.get("chunk_type", "raw"),
                   name=meta.get("name", ""), start_line=meta.get("start_line", 0),
                   end_line=meta.get("end_line", 0), code=code, distance=distance, expanded=expanded)

    def as_context_block(self) -> str:
        if self.chunk_type == "overview":
            return f"[{self.file_path}]\n{self.code}"
        location = f"{self.file_path} (líneas {self.start_line}-{self.end_line})"
        label = f"{self.chunk_type} '{self.name}'" if self.name else self.chunk_type
        tag = " [incluido automáticamente por grafo de llamadas]" if self.expanded else ""
        # Each chunk is labeled with its path rather than a number, so the model cites the file.
        return f"[{location}] {label}{tag}\n```\n{self.code}\n```"


def question_identifiers(question: str) -> List[str]:
    """Words in the question shaped like code identifiers: snake_case, camelCase/PascalCase or
    CONSTANT_CASE. Common words (Spanish or English) do not have that shape.
    """
    found = []
    for token in QUESTION_TOKEN.findall(question):
        if ("_" in token.strip("_") or re.search(r"[a-z][A-Z]", token)) and token not in found:
            found.append(token)
    return found


def _cosine_distance(a, b) -> float:
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    norm = np.linalg.norm(a) * np.linalg.norm(b)
    return float(1 - a.dot(b) / norm) if norm else 1.0


class Retriever:
    """Searches the code collection of one project. `embed` turns a question into its vector."""

    def __init__(self, client, embed: Callable[[str], List[float]]):
        self.client = client
        self.embed = embed
        self.collection = open_code_collection(client)

    def refresh_collection(self):
        """A full rebuild (in this or another process) replaces the collection, and the stored reference points to the deleted one."""
        self.collection = open_code_collection(self.client)

    def query(self, embedding: List[float], n: int) -> dict:
        """With many deleted elements in the HNSW graph, hnswlib may fail to gather `n` neighbors; the query is
        then retried asking for fewer. The cause is avoided when the index is rebuilt (see CodebaseIndexer.sync):
        this is only a safety net.
        """
        while True:
            try:
                return self.collection.query(query_embeddings=[embedding], n_results=n)
            except RuntimeError as e:
                if "contigious" not in str(e) or n <= 1:
                    raise
                logger.warning("El índice no devolvió %d candidatos; se reintenta con %d. "
                               "Un 'beacon sync --full' lo reconstruye.", n, n // 2)
                n //= 2

    def _semantic(self, embedding: List[float], n: int):
        count = self.collection.count()
        return self.query(embedding, min(n, count)) if count else None

    def ranked_candidates(self, question: str, n: int) -> List[RetrievedChunk]:
        embedding = self.embed(question)
        try:
            results = self._semantic(embedding, n)
        except InvalidCollectionException:
            self.refresh_collection()  # replaced during a rebuild
            results = self._semantic(embedding, n)
        if results is None:
            return []
        rows = {chunk_id: [doc, meta, dist] for chunk_id, doc, meta, dist in zip(
            results["ids"][0], results["documents"][0], results["metadatas"][0], results["distances"][0])}
        for chunk_id, (doc, meta, dist, bonus) in self.identifier_matches(question, embedding).items():
            rows.setdefault(chunk_id, [doc, meta, dist])[2] = dist - bonus
        penalize_tests = not ASKS_ABOUT_TESTS.search(question)
        chunks = []
        for doc, meta, dist in rows.values():
            if penalize_tests and TEST_PATH_PATTERN.search(meta.get("file_path", "?")):
                dist += TEST_DISTANCE_PENALTY
            chunks.append(RetrievedChunk.from_index(doc, meta, dist))
        chunks.sort(key=lambda c: c.distance)
        return chunks

    def identifier_matches(self, question: str, embedding: List[float]) -> dict:
        """Chunks named like an identifier of the question or using it in their code, with their real
        distance and discount. It uses filtered `get` and computes the distance here because filtered
        hnswlib queries fail when there are fewer matches than requested results.
        """
        identifiers = question_identifiers(question)
        if not identifiers:
            return {}
        found = {}
        name_bonus, code_bonus = NAME_MATCH_BONUS, CODE_MATCH_BONUS
        if ASKS_ABOUT_USAGE.search(question):
            name_bonus, code_bonus = CODE_MATCH_BONUS, NAME_MATCH_BONUS

        def add(result, bonus):
            for chunk_id, doc, meta, vector in zip(result["ids"], result["documents"],
                                                   result["metadatas"], result["embeddings"]):
                if chunk_id not in found or found[chunk_id][3] < bonus:
                    found[chunk_id] = (doc, meta, _cosine_distance(embedding, vector), bonus)

        name_filter = {"name": identifiers[0]} if len(identifiers) == 1 else {"name": {"$in": identifiers}}
        add(self.collection.get(where=name_filter, include=_INCLUDE_WITH_VECTORS), name_bonus)
        for identifier in identifiers:
            in_code = self.collection.get(where_document={"$contains": identifier}, include=_INCLUDE_WITH_VECTORS,
                                          limit=IDENTIFIER_MAX_CODE_MATCHES + 1)
            if len(in_code["ids"]) <= IDENTIFIER_MAX_CODE_MATCHES:
                add(in_code, code_bonus)
        return found

    def retrieve(self, question: str, top_k: int = DEFAULT_TOP_K) -> List[RetrievedChunk]:
        """More candidates than top_k are requested because the test penalty reorders the list."""
        return self.ranked_candidates(question, max(top_k * 4, 20))[:top_k]

    def select_context(self, question: str, top_k: int = DEFAULT_TOP_K) -> List[RetrievedChunk]:
        """top_k chunks plus those nearly tied with the last one (see ADAPTIVE_MARGIN): the context only
        grows when the question has several equally relevant answers.
        """
        candidates = self.ranked_candidates(question, max(top_k * 4, 20))
        chosen = candidates[:top_k]
        if chosen:
            limit = chosen[-1].distance + ADAPTIVE_MARGIN
            chosen += [c for c in candidates[top_k:] if c.distance <= limit][:ADAPTIVE_MAX_EXTRA]
        return chosen

    def chunks_named(self, name: str) -> List[RetrievedChunk]:
        """Every chunk whose name is exactly `name` (distance -1: not ranked by similarity)."""
        result = self.collection.get(where={"name": name}, include=["documents", "metadatas"])
        return [RetrievedChunk.from_index(doc, meta, -1.0) for doc, meta in zip(result["documents"], result["metadatas"])]
