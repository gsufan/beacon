"""Motor RAG: recuperación semántica + expansión por grafo de llamadas + LLM local."""

import logging
import re
from dataclasses import dataclass

import numpy as np
from typing import List, Optional

from chromadb.errors import InvalidCollectionException

from core.engine.chroma_utils import get_chroma_client
import ollama

from core.config import AIProviderConfig
from core.projects import ProjectContext
from core.engine.embeddings import embed_query
from core.engine.indexer import (
    COLLECTION_NAME, CONTROL_COLLECTION_NAME, IndexModelMismatchError, indexed_embedding_model,
)
from core.engine.llm import chat, estimate_tokens, prompt_budget_tokens
from core.engine.scope_resolution import resolve_candidate
from core.engine.text_sanitize import strip_preamble

logger = logging.getLogger("beacon.rag")

DEFAULT_TOP_K = 5
DEFAULT_MAX_CALL_EXPANSIONS = 5
# Tokens que se dejan libres para la respuesta dentro de la ventana del modelo.
ANSWER_RESERVE_TOKENS = 1024

# Los tests repiten literalmente las palabras de la pregunta en sus nombres
# (test_http_303_changes_post_to_get) y le ganaban a la función que la
# responde. Se les suma esta distancia salvo que la pregunta sea sobre tests.
# Medido con eval/requests.yaml: Hit@5 subió de 80% a 93% con qwen3-embedding.
TEST_DISTANCE_PENALTY = 0.08
TEST_PATH_PATTERN = re.compile(r"(^|/)(tests?|testing|__tests__)/|(^|/)test_[^/]*$|_test\.[a-z]+$|\.(test|spec)\.[jt]sx?$")
ASKS_ABOUT_TESTS = re.compile(r"\b(tests?|testing|prueba|pruebas|testear|spec)\b", re.IGNORECASE)

# Contexto adaptativo: además de los top_k, se suman fragmentos casi tan
# relevantes como el último elegido (dentro de este margen de distancia),
# hasta este máximo. Así una pregunta que toca varios archivos recibe más
# contexto sin inflar las preguntas puntuales. El presupuesto de tokens
# (_fit_to_budget) sigue siendo el tope final.
ADAPTIVE_MARGIN = 0.03
ADAPTIVE_MAX_EXTRA = 5

# Búsqueda híbrida: si la pregunta nombra un identificador del código
# (`super_len`, `TooManyRedirects`, `DEFAULT_CA_BUNDLE_PATH`), se suman como
# candidatos los fragmentos que se llaman así o que lo usan, aunque la
# búsqueda semántica no los haya traído, y se les resta distancia. Sin esto,
# los tests con el identificador en su nombre le ganaban a la función, y en
# preguntas de uso ("¿dónde se usa max_redirects?") aparecía la definición
# pero no quien la usa.
NAME_MATCH_BONUS = 0.10
CODE_MATCH_BONUS = 0.04
# Un identificador presente en más fragmentos que esto es demasiado común
# para orientar la búsqueda (ej. `self`, `request`): se ignora su aparición
# en el código.
IDENTIFIER_MAX_CODE_MATCHES = 15
QUESTION_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]{3,}")
# "¿Dónde se usa X?", "¿quién llama a X?", "¿cuándo se lanza X?": se pregunta
# por quien usa el identificador, no por su definición, así que el descuento
# fuerte va a los fragmentos que lo usan.
ASKS_ABOUT_USAGE = re.compile(
    r"\b(d[oó]nde|qui[eé]n|qu[eé]|cu[aá]ndo)\b.{0,25}\b(usa|usan|utiliza|llama|llaman|invoca|lanza|lanzan|"
    r"levanta|produce|lee|leen|modifica|asigna)\b", re.IGNORECASE)

# Candidatos a llamada: `nombre(`, salvo cuando es una definición
# (`def hook(` dentro de un test no es una llamada a otro `hook`). Se comparó
# con extraer las llamadas del AST en todo el índice de los dos repositorios
# de evaluación: coinciden en el 98% (requests) y 92% (microservices-demo) de
# las expansiones, y casi todas las diferencias están en tests; fuera de
# ellos, el AST evitaría 3 expansiones de más. No justifica mantener reglas
# de extracción para cada lenguaje (detalle en docs/ARQUITECTURA.md).
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
        location = f"{self.file_path} (líneas {self.start_line}-{self.end_line})"
        label = f"{self.chunk_type} '{self.name}'" if self.name else self.chunk_type
        tag = " [incluido automáticamente por grafo de llamadas]" if self.expanded else ""
        # Sin número de fragmento: el modelo tendía a citar "el fragmento 3",
        # que el usuario no ve; si la etiqueta es la ruta, cita la ruta.
        return f"[{location}] {label}{tag}\n```\n{self.code}\n```"


@dataclass
class RAGResponse:
    answer: str
    sources: List[RetrievedChunk]
    # Tokens que Ollama reporta haber procesado (0 si no los informa); los usa
    # tools/eval_answers.py para confirmar que ningún prompt se truncó.
    prompt_tokens: int = 0
    answer_tokens: int = 0

    def to_dict(self) -> dict:
        return {
            "answer": self.answer,
            "sources": [
                {"file_path": s.file_path, "chunk_type": s.chunk_type, "name": s.name,
                 "start_line": s.start_line, "end_line": s.end_line,
                 "distance": round(s.distance, 4), "expanded": s.expanded}
                for s in self.sources
            ],
        }


class RAGEngine:
    def __init__(self, project: ProjectContext, ai_config: AIProviderConfig):
        self.project = project
        self.ai_config = ai_config
        self.client_ollama = ollama.Client(host=ai_config.ollama_host)
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
        """Candidatos ordenados por distancia: búsqueda semántica más los
        fragmentos que coinciden con identificadores de la pregunta, con la
        penalización a tests."""
        self._check_index_model()
        embedding = embed_query(self.client_ollama, self.ai_config.embedding_model, question)
        try:
            count = self.collection.count()
            results = self._query(embedding, min(n, count)) if count else None
        except InvalidCollectionException:
            self._refresh_collection()  # se reemplazó durante una reconstrucción
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
        """Palabras de la pregunta con forma de identificador de código:
        snake_case, camelCase/PascalCase o CONSTANTE_CON_GUION. Las palabras
        comunes (en español o en inglés) no tienen esa forma."""
        found = []
        for token in QUESTION_TOKEN.findall(question):
            if ("_" in token.strip("_") or re.search(r"[a-z][A-Z]", token)) and token not in found:
                found.append(token)
        return found

    def _identifier_matches(self, question: str, embedding: List[float]) -> dict:
        """Fragmentos que se llaman como un identificador de la pregunta o lo
        usan en su código, con su distancia real a la pregunta y el descuento
        que les corresponde. Se usan `get` con filtros y la distancia se
        calcula aquí: las consultas filtradas de hnswlib fallan cuando hay
        menos coincidencias que resultados pedidos."""
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
        """Vuelve a buscar la colección por nombre: una reconstrucción completa
        (en este u otro proceso) la reemplaza por una nueva y la referencia
        guardada apunta a la anterior, ya borrada."""
        self.collection = self.client.get_or_create_collection(name=COLLECTION_NAME,
                                                               metadata={"hnsw:space": "cosine"})

    def _query(self, embedding: List[float], n: int) -> dict:
        """Consulta a ChromaDB. Con muchos elementos borrados en el grafo HNSW,
        hnswlib puede no reunir `n` vecinos y falla ("Cannot return the results
        in a contigious 2D array"); se observó una vez tras varias
        reconstrucciones seguidas. En vez de perder la consulta se reintenta
        pidiendo menos candidatos. La causa se evita al reconstruir (ver
        CodebaseIndexer.sync), esto es solo la red de seguridad."""
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
        """Los top_k fragmentos más relevantes. Se piden más candidatos que
        top_k porque la penalización de tests reordena la lista."""
        return self._ranked_candidates(question, max(top_k * 4, 20))[:top_k]

    def select_context(self, question: str, top_k: int = DEFAULT_TOP_K) -> List[RetrievedChunk]:
        """top_k fragmentos más los que quedan casi empatados con el último
        (ver ADAPTIVE_MARGIN): el contexto crece solo cuando la pregunta tiene
        varias respuestas igual de relevantes."""
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

    def _build_prompt(self, question: str, chunks: List[RetrievedChunk]) -> str:
        context = "(No se encontraron fragmentos relevantes.)" if not chunks else \
            "\n\n".join(c.as_context_block(i + 1) for i, c in enumerate(chunks))
        return f"CONTEXTO:\n\n{context}\n\n---\n\nPREGUNTA: {question}\n\nResponde siguiendo las reglas del sistema."

    def _fit_to_budget(self, question: str, system_prompt: str,
                       chunks: List[RetrievedChunk]) -> List[RetrievedChunk]:
        """Deja solo los fragmentos que caben en la ventana del modelo, en orden
        de prioridad (primero los recuperados por similitud, luego los del grafo
        de llamadas). Mejor entregar menos contexto completo que un prompt que
        Ollama trunque por el comienzo, perdiendo las reglas del sistema."""
        budget = prompt_budget_tokens(ANSWER_RESERVE_TOKENS)
        used = estimate_tokens(system_prompt) + estimate_tokens(self._build_prompt(question, []))
        kept: List[RetrievedChunk] = []
        for chunk in chunks:
            cost = estimate_tokens(chunk.as_context_block(len(kept) + 1)) + 1
            if used + cost > budget:
                continue  # uno más chico que venga después todavía puede caber
            kept.append(chunk)
            used += cost
        return kept

    def ask(
        self, question: str, top_k: int = DEFAULT_TOP_K, expand_call_graph: bool = True, language: str = "es"
    ) -> RAGResponse:
        chunks = self.select_context(question, top_k=top_k)
        if expand_call_graph:
            chunks = chunks + self.expand_with_call_graph(chunks)
        language_instruction = LANGUAGE_INSTRUCTIONS.get(language, LANGUAGE_INSTRUCTIONS["es"])
        system_prompt = language_instruction + "\n\n" + SYSTEM_PROMPT
        # Las fuentes que se devuelven son exactamente las que vio el modelo.
        chunks = self._fit_to_budget(question, system_prompt, chunks)
        prompt = self._build_prompt(question, chunks)
        result = chat(self.client_ollama, self.ai_config.llm_model, system_prompt, prompt)
        answer = strip_preamble(result.content, heading_marker="\x00")
        return RAGResponse(answer=answer, sources=chunks,
                           prompt_tokens=result.prompt_tokens, answer_tokens=result.answer_tokens)
