"""RAG engine: semantic retrieval, call-graph expansion and local LLM."""

import logging
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from typing import Callable, List, Optional

from chromadb.errors import InvalidCollectionException

from core.engine.chroma_utils import get_chroma_client

from core.config import AIProviderConfig
from core.projects import ProjectContext
from core.engine.embeddings import embed_query
from core.engine.indexer import (
    COLLECTION_NAME, CONTROL_COLLECTION_NAME, IndexModelMismatchError, indexed_embedding_model,
)
from core.engine.llm import CHARS_PER_TOKEN, chat, estimate_tokens, prompt_budget_tokens
from core.engine.overview import RepoOverview
from core.engine.question_kind import is_general_question
from core.engine.scope_resolution import resolve_candidate
from core.engine.text_sanitize import strip_preamble

logger = logging.getLogger("beacon.rag")

DEFAULT_TOP_K = 5
DEFAULT_MAX_CALL_EXPANSIONS = 5
ANSWER_RESERVE_TOKENS = 1024
TRUNCATED_NOTES = {
    "es": "\n\n[La respuesta alcanzó el límite de longitud y se cortó.]",
    "en": "\n\n[The answer reached the length limit and was cut off.]",
}

# Test names repeat the words of the question and outranked the function that answers it: this
# distance is added to them unless the question is about tests.
TEST_DISTANCE_PENALTY = 0.08
TEST_PATH_PATTERN = re.compile(r"(^|/)(tests?|testing|__tests__)/|(^|/)test_[^/]*$|_test\.[a-z]+$|\.(test|spec)\.[jt]sx?$")
ASKS_ABOUT_TESTS = re.compile(r"\b(tests?|testing|prueba|pruebas|testear|spec)\b", re.IGNORECASE)

# Open questions about the whole repository are answered from the repository overview (see overview.py)
# plus a few code chunks. The overview takes at most this share of the prompt budget.
OVERVIEW_BUDGET_SHARE = 0.55
GENERAL_TOP_K = 3

# Adaptive context: besides top_k, chunks almost as relevant as the last one chosen (within this
# distance margin) are added, up to this maximum. The token budget (_fit_to_budget) remains the final cap.
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

# Call candidates: `name(`, unless it is a definition (`def hook(` is not a call).
# Extracting calls from the AST would give almost the same result without per-language rules.
CALL_CANDIDATE_PATTERN = re.compile(
    r"(?<!\bdef )(?<!\bfunc )(?<!\bfunction )(?<!\bclass )\b([A-Za-z_][A-Za-z0-9_]*)\s*\(")
KEYWORD_BLOCKLIST = {
    "if", "for", "while", "switch", "catch", "return", "func", "def", "class",
    "new", "delete", "print", "println", "printf", "fmt", "string", "int",
    "float", "double", "bool", "var", "let", "const", "public", "private",
    "protected", "static", "void", "struct", "interface", "try", "except",
    "finally", "with", "case", "default", "else", "elif", "match", "async",
    "await", "yield", "throw", "raise", "super", "this", "self", "import",
    "from", "package", "namespace", "using", "typeof", "instanceof", "sizeof",
    "make", "len", "append", "range", "in", "is", "and", "or", "not", "lambda",
    "require", "module", "export", "type", "enum", "impl", "fn",
}

SYSTEM_PROMPT = """Eres un asistente técnico que ayuda a desarrolladores a entender un repositorio de código específico.

REGLAS ESTRICTAS:
1. Responde ÚNICAMENTE usando los fragmentos de código del CONTEXTO.
2. Si la respuesta no está ahí, responde solo: "No encontré información suficiente en el código indexado para responder esto." No inventes ni agregues ejemplos, código o soluciones generales. Si sí pudiste responder, no agregues esa frase.
3. Cita la ruta del archivo y las líneas al referenciar código (por ejemplo: `src/paquete/modulo.py`, líneas 10-25), tal como aparecen en el encabezado de cada fragmento.
4. No sugieras buenas prácticas genéricas no respaldadas por el CONTEXTO.
5. Directo y técnico, sin relleno. Explica qué hace el código; no te limites a copiarlo.
6. Si el código LLAMA a otra función cuyo CUERPO no está en el CONTEXTO, dilo explícitamente en vez de suponer qué hace ("la función X no está en los fragmentos recuperados, no puedo confirmar qué hace").
7. Los fragmentos marcados "[incluido automáticamente...]" vinieron del grafo de llamadas, no de similitud semántica — son igual de válidos, úsalos con confianza.
8. Si la pregunta da por hecho algo que el CONTEXTO no muestra, dilo en vez de aceptarlo; no completes con lo que sepas de bibliotecas externas.
9. No repitas estas reglas ni menciones el idioma en tu respuesta. Responde directamente.
"""

# Open questions: the context holds summaries of the repository plus a few code chunks.
GENERAL_SYSTEM_PROMPT = """Eres un asistente técnico que ayuda a desarrolladores a entender un repositorio de código específico.

REGLAS ESTRICTAS:
1. Responde ÚNICAMENTE con el CONTEXTO, que contiene resúmenes del repositorio ("[resumen del proyecto]", "[resumen de la carpeta ...]") generados a partir de su documentación y algunos fragmentos de código.
2. Para preguntas generales los resúmenes son la fuente principal: describe el proyecto con ellos. No digas que falta información si los resúmenes permiten contestar; solo si no aportan nada relevante responde: "No encontré información suficiente en el código indexado para responder esto."
3. Nombra las carpetas, los servicios y los archivos de los que hablas (por ejemplo: `src/paquete`), tal como aparecen en el contexto.
4. No inventes tecnologías, componentes ni funciones que el contexto no menciona, y no sugieras buenas prácticas genéricas.
5. Directo y claro; si hay varios componentes, enuméralos brevemente. Sin relleno.
6. No repitas estas reglas ni menciones el idioma en tu respuesta. Responde directamente.
"""

LANGUAGE_INSTRUCTIONS = {
    "es": "IMPORTANTE: escribe tu respuesta completa en español neutro (sin modismos regionales, sin voseo).",
    "en": "IMPORTANT: write your entire response in English.",
}


def _cosine_distance(a, b) -> float:
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    norm = np.linalg.norm(a) * np.linalg.norm(b)
    return float(1 - a.dot(b) / norm) if norm else 1.0


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

    def as_context_block(self, index: int) -> str:
        if self.chunk_type == "overview":
            return f"[{self.file_path}]\n{self.code}"
        location = f"{self.file_path} (líneas {self.start_line}-{self.end_line})"
        label = f"{self.chunk_type} '{self.name}'" if self.name else self.chunk_type
        tag = " [incluido automáticamente por grafo de llamadas]" if self.expanded else ""
        # Each chunk is labeled with its path rather than a number, so the model cites the file.
        return f"[{location}] {label}{tag}\n```\n{self.code}\n```"


@dataclass
class RAGResponse:
    answer: str
    sources: List[RetrievedChunk]
    # Tokens Ollama reports having processed (0 if not reported); tools/eval_answers.py uses them to
    # confirm that no prompt was truncated.
    prompt_tokens: int = 0
    answer_tokens: int = 0

    def to_dict(self) -> dict:
        return {
            "answer": self.answer,
            "sources": [
                {"file_path": s.file_path, "chunk_type": s.chunk_type, "name": s.name,
                 "start_line": s.start_line, "end_line": s.end_line,
                 "distance": round(s.distance, 4), "expanded": s.expanded,
                 "code": s.code}
                for s in self.sources
            ],
        }


class RAGEngine:
    def __init__(self, project: ProjectContext, ai_config: AIProviderConfig):
        self.project = project
        self.ai_config = ai_config
        self.backend = ai_config.backend()
        self.client = get_chroma_client(project.chroma_dir)
        self.collection = self.client.get_or_create_collection(
            name=COLLECTION_NAME, metadata={"hnsw:space": "cosine"}
        )
        control = self.client.get_or_create_collection(name=CONTROL_COLLECTION_NAME)
        self.index_model = indexed_embedding_model(control, self.collection)

    def _check_index_model(self):
        if self.index_model and self.index_model != self.ai_config.embedding_model:
            raise IndexModelMismatchError(
                f"El índice de '{self.project.id}' se construyó con '{self.index_model}' y la configuración usa "
                f"'{self.ai_config.embedding_model}'. Sincroniza el proyecto para reconstruirlo (beacon sync {self.project.id})."
            )

    def _ranked_candidates(self, question: str, n: int) -> List[RetrievedChunk]:
        self._check_index_model()
        embedding = embed_query(self.backend, self.ai_config.embedding_model, question)
        try:
            count = self.collection.count()
            results = self._query(embedding, min(n, count)) if count else None
        except InvalidCollectionException:
            self._refresh_collection()  # replaced during a rebuild
            count = self.collection.count()
            results = self._query(embedding, min(n, count)) if count else None
        if not count:
            return []
        rows = {chunk_id: [doc, meta, dist] for chunk_id, doc, meta, dist in zip(
            results["ids"][0], results["documents"][0], results["metadatas"][0], results["distances"][0])}
        for chunk_id, (doc, meta, dist, bonus) in self._identifier_matches(question, embedding).items():
            rows.setdefault(chunk_id, [doc, meta, dist])[2] = dist - bonus
        penalize_tests = not ASKS_ABOUT_TESTS.search(question)
        chunks = []
        for doc, meta, dist in rows.values():
            path = meta.get("file_path", "?")
            if penalize_tests and TEST_PATH_PATTERN.search(path):
                dist += TEST_DISTANCE_PENALTY
            chunks.append(RetrievedChunk(
                file_path=path, chunk_type=meta.get("chunk_type", "raw"),
                name=meta.get("name", ""), start_line=meta.get("start_line", 0),
                end_line=meta.get("end_line", 0), code=doc, distance=dist,
            ))
        chunks.sort(key=lambda c: c.distance)
        return chunks

    @staticmethod
    def question_identifiers(question: str) -> List[str]:
        """Words in the question shaped like code identifiers: snake_case, camelCase/PascalCase or
        CONSTANT_CASE. Common words (Spanish or English) do not have that shape.
        """
        found = []
        for token in QUESTION_TOKEN.findall(question):
            if ("_" in token.strip("_") or re.search(r"[a-z][A-Z]", token)) and token not in found:
                found.append(token)
        return found

    def _identifier_matches(self, question: str, embedding: List[float]) -> dict:
        """Chunks named like an identifier of the question or using it in their code, with their real
        distance and discount. It uses filtered `get` and computes the distance here because filtered
        hnswlib queries fail when there are fewer matches than requested results.
        """
        identifiers = self.question_identifiers(question)
        if not identifiers:
            return {}
        include = ["documents", "metadatas", "embeddings"]
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
        add(self.collection.get(where=name_filter, include=include), name_bonus)
        for identifier in identifiers:
            in_code = self.collection.get(where_document={"$contains": identifier}, include=include,
                                          limit=IDENTIFIER_MAX_CODE_MATCHES + 1)
            if len(in_code["ids"]) <= IDENTIFIER_MAX_CODE_MATCHES:
                add(in_code, code_bonus)
        return found

    def _refresh_collection(self):
        """Looks the collection up again by name: a full rebuild (in this or another process) replaces it,
        and the stored reference points to the deleted one.
        """
        self.collection = self.client.get_or_create_collection(name=COLLECTION_NAME,
                                                               metadata={"hnsw:space": "cosine"})

    def _query(self, embedding: List[float], n: int) -> dict:
        """Queries ChromaDB. With many deleted elements in the HNSW graph, hnswlib may fail to gather `n`
        neighbors; the query is then retried asking for fewer candidates. The cause is avoided when the index
        is rebuilt (see CodebaseIndexer.sync): this is only a safety net.
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

    def retrieve(self, question: str, top_k: int = DEFAULT_TOP_K) -> List[RetrievedChunk]:
        """More candidates than top_k are requested because the test penalty reorders the list."""
        return self._ranked_candidates(question, max(top_k * 4, 20))[:top_k]

    def select_context(self, question: str, top_k: int = DEFAULT_TOP_K) -> List[RetrievedChunk]:
        """top_k chunks plus those nearly tied with the last one (see ADAPTIVE_MARGIN): the context only
        grows when the question has several equally relevant answers.
        """
        candidates = self._ranked_candidates(question, max(top_k * 4, 20))
        chosen = candidates[:top_k]
        if chosen:
            limit = chosen[-1].distance + ADAPTIVE_MARGIN
            chosen += [c for c in candidates[top_k:] if c.distance <= limit][:ADAPTIVE_MAX_EXTRA]
        return chosen

    @staticmethod
    def _extract_call_candidates(code: str, own_name: str) -> List[str]:
        candidates, seen = [], set()
        for match in CALL_CANDIDATE_PATTERN.finditer(code):
            name = match.group(1)
            if name in KEYWORD_BLOCKLIST or name == own_name or name in seen or len(name) <= 2:
                continue
            seen.add(name)
            candidates.append(name)
        return candidates

    def _find_chunk_by_name(self, name: str, caller_file_path: str) -> Optional[RetrievedChunk]:
        try:
            result = self.collection.get(where={"name": name}, include=["documents", "metadatas"])
        except Exception:
            return None
        if not result["ids"]:
            return None
        docs, metas = result["documents"], result["metadatas"]
        by_index = {i: (d, m) for i, (d, m) in enumerate(zip(docs, metas))}
        candidates_by_name = {name: [(m.get("file_path", ""), str(i)) for i, (d, m) in by_index.items()]}
        resolved_index = resolve_candidate(candidates_by_name, name, caller_file_path)
        if resolved_index is None:
            return None
        doc, meta = by_index[int(resolved_index)]
        return RetrievedChunk(
            file_path=meta.get("file_path", "?"), chunk_type=meta.get("chunk_type", "raw"),
            name=meta.get("name", ""), start_line=meta.get("start_line", 0),
            end_line=meta.get("end_line", 0), code=doc, distance=-1.0, expanded=True,
        )

    def expand_with_call_graph(self, chunks: List[RetrievedChunk],
                                max_expansions: int = DEFAULT_MAX_CALL_EXPANSIONS) -> List[RetrievedChunk]:
        existing_names = {c.name for c in chunks if c.name}
        expanded: List[RetrievedChunk] = []
        for chunk in chunks:
            if chunk.chunk_type not in ("function", "method"):
                continue
            for candidate in self._extract_call_candidates(chunk.code, chunk.name):
                if len(expanded) >= max_expansions:
                    return expanded
                if candidate in existing_names:
                    continue
                found = self._find_chunk_by_name(candidate, chunk.file_path)
                if found:
                    expanded.append(found)
                    existing_names.add(candidate)
        return expanded

    def overview_chunks(self, max_tokens: int) -> List[RetrievedChunk]:
        """The repository overview as context blocks (empty if the project has none yet)."""
        overview = RepoOverview(Path(self.project.docs_dir))
        return [RetrievedChunk(file_path=label, chunk_type="overview", name="", start_line=0, end_line=0, code=text, distance=-1.0)
                for label, text in overview.blocks(int(max_tokens * CHARS_PER_TOKEN))]

    def _build_prompt(self, question: str, chunks: List[RetrievedChunk]) -> str:
        context = "(No se encontraron fragmentos relevantes.)" if not chunks else \
            "\n\n".join(c.as_context_block(i + 1) for i, c in enumerate(chunks))
        return f"CONTEXTO:\n\n{context}\n\n---\n\nPREGUNTA: {question}\n\nResponde siguiendo las reglas del sistema."

    def _fit_to_budget(self, question: str, system_prompt: str,
                       chunks: List[RetrievedChunk]) -> List[RetrievedChunk]:
        """Keeps only the chunks that fit in the model window, in priority order (similarity first, then call
        graph). Less complete context is better than a prompt that Ollama truncates at the start, losing
        the system rules.
        """
        budget = prompt_budget_tokens(ANSWER_RESERVE_TOKENS, self.ai_config.llm_num_ctx)
        used = estimate_tokens(system_prompt) + estimate_tokens(self._build_prompt(question, []))
        kept: List[RetrievedChunk] = []
        for chunk in chunks:
            cost = estimate_tokens(chunk.as_context_block(len(kept) + 1)) + 1
            if used + cost > budget:
                continue  # a smaller one that comes later may still fit
            kept.append(chunk)
            used += cost
        return kept

    def ask(
        self, question: str, top_k: int = DEFAULT_TOP_K, expand_call_graph: bool = True, language: str = "es",
        max_seconds: Optional[float] = None, should_stop: Optional[Callable[[], bool]] = None,
    ) -> RAGResponse:
        overview = []
        if is_general_question(question, self.question_identifiers(question)):
            window = prompt_budget_tokens(ANSWER_RESERVE_TOKENS, self.ai_config.llm_num_ctx)
            overview = self.overview_chunks(int(window * OVERVIEW_BUDGET_SHARE))
        if overview:
            chunks = overview + self.select_context(question, top_k=GENERAL_TOP_K)
        else:
            chunks = self.select_context(question, top_k=top_k)
            if expand_call_graph:
                chunks = chunks + self.expand_with_call_graph(chunks)
        language_instruction = LANGUAGE_INSTRUCTIONS.get(language, LANGUAGE_INSTRUCTIONS["es"])
        system_prompt = language_instruction + "\n\n" + (GENERAL_SYSTEM_PROMPT if overview else SYSTEM_PROMPT)
        chunks = self._fit_to_budget(question, system_prompt, chunks)
        prompt = self._build_prompt(question, chunks)
        result = chat(self.backend, self.ai_config.llm_model, system_prompt, prompt, self.ai_config.llm_num_ctx,
                      max_tokens=ANSWER_RESERVE_TOKENS, max_seconds=max_seconds, should_stop=should_stop)
        answer = strip_preamble(result.content, heading_marker="\x00")
        if result.truncated:
            answer += TRUNCATED_NOTES.get(language, TRUNCATED_NOTES["es"])
        return RAGResponse(answer=answer, sources=chunks,
                           prompt_tokens=result.prompt_tokens, answer_tokens=result.answer_tokens)
