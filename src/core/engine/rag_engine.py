"""Motor RAG: recuperación semántica + expansión por grafo de llamadas + LLM local."""

import re
from dataclasses import dataclass
from typing import List, Optional

from core.engine.chroma_utils import get_chroma_client
import ollama

from core.config import AIProviderConfig
from core.projects import ProjectContext
from core.engine.indexer import COLLECTION_NAME

DEFAULT_TOP_K = 5
DEFAULT_MAX_CALL_EXPANSIONS = 5

CALL_CANDIDATE_PATTERN = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(")
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
2. Si la respuesta no está ahí, dilo explícitamente: "No encontré información suficiente en el código indexado para responder esto." No inventes.
3. Cita archivo y líneas exactas al referenciar código.
4. No sugieras buenas prácticas genéricas no respaldadas por el CONTEXTO.
5. Directo y técnico, sin relleno.
6. Si el código LLAMA a otra función cuyo CUERPO no está en el CONTEXTO, dilo explícitamente en vez de suponer qué hace ("la función X no está en los fragmentos recuperados, no puedo confirmar qué hace").
7. Los fragmentos marcados "[incluido automáticamente...]" vinieron del grafo de llamadas, no de similitud semántica — son igual de válidos, úsalos con confianza.
"""


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
        return f"[Fragmento {index}] {label} — {location}{tag}\n```\n{self.code}\n```"


@dataclass
class RAGResponse:
    answer: str
    sources: List[RetrievedChunk]

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

    def _embed_query(self, text: str) -> List[float]:
        response = self.client_ollama.embeddings(
            model=self.ai_config.embedding_model, prompt=f"search_query: {text}"
        )
        return response["embedding"]

    def retrieve(self, question: str, top_k: int = DEFAULT_TOP_K) -> List[RetrievedChunk]:
        if self.collection.count() == 0:
            return []
        query_embedding = self._embed_query(question)
        results = self.collection.query(
            query_embeddings=[query_embedding], n_results=min(top_k, self.collection.count())
        )
        chunks = []
        for doc, meta, dist in zip(results["documents"][0], results["metadatas"][0], results["distances"][0]):
            chunks.append(RetrievedChunk(
                file_path=meta.get("file_path", "?"), chunk_type=meta.get("chunk_type", "raw"),
                name=meta.get("name", ""), start_line=meta.get("start_line", 0),
                end_line=meta.get("end_line", 0), code=doc, distance=dist,
            ))
        return chunks

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
        if len(docs) == 1:
            doc, meta = docs[0], metas[0]
        else:
            scope_prefix = "/".join(caller_file_path.split("/")[:2])
            same_scope = [(d, m) for d, m in zip(docs, metas) if m.get("file_path", "").startswith(scope_prefix)]
            if len(same_scope) != 1:
                return None
            doc, meta = same_scope[0]
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

    def ask(self, question: str, top_k: int = DEFAULT_TOP_K, expand_call_graph: bool = True) -> RAGResponse:
        chunks = self.retrieve(question, top_k=top_k)
        if expand_call_graph:
            chunks = chunks + self.expand_with_call_graph(chunks)
        prompt = self._build_prompt(question, chunks)
        response = self.client_ollama.chat(
            model=self.ai_config.llm_model,
            messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}],
        )
        return RAGResponse(answer=response["message"]["content"], sources=chunks)
