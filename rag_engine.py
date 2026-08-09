"""
Motor RAG — Pilar 4D de la propuesta.

Recibe una pregunta en lenguaje natural, recupera los fragmentos de código
más relevantes desde ChromaDB (mismo índice que llena indexer.py), y le pide
al LLM local que responda ÚNICAMENTE en base a esos fragmentos.

Anti-alucinaciones: el prompt es explícito en instruir al modelo a decir
"no lo sé" si la respuesta no está en el contexto recuperado, en vez de
inventar una solución que suene plausible pero no exista en el repositorio.
"""

import os
import re
from dataclasses import dataclass, field
from typing import List, Optional

import chromadb
import ollama
from dotenv import load_dotenv

load_dotenv()

EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "nomic-embed-text")
LLM_MODEL = os.getenv("LLM_MODEL", "llama3:8b")
CHROMA_DIR = os.getenv("CHROMA_PERSIST_DIR", "./data/chroma_db")
COLLECTION_NAME = os.getenv("CHROMA_COLLECTION_NAME", "codebase_index")

DEFAULT_TOP_K = 5
DEFAULT_MAX_CALL_EXPANSIONS = 5

# Detecta candidatos a llamada de función: un identificador seguido de "(".
# Es una heurística por regex (no un parseo AST por lenguaje) a propósito:
# cubrir los 8 lenguajes soportados con nodos de "llamada" exactos por
# gramática sería mucho más código para una ganancia marginal. El filtro de
# keywords de abajo compensa el ruido de falsos positivos.
CALL_CANDIDATE_PATTERN = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(")

# Palabras reservadas/comunes de los lenguajes soportados que NO son llamadas
# a función propias del repositorio, para no perseguir falsos positivos como
# "if(" o "print(".
KEYWORD_BLOCKLIST = {
    "if", "for", "while", "switch", "catch", "return", "func", "def", "class",
    "new", "delete", "print", "println", "printf", "fmt", "string", "int",
    "float", "double", "bool", "var", "let", "const", "public", "private",
    "protected", "static", "void", "struct", "interface", "try", "except",
    "finally", "with", "case", "default", "else", "elif", "match", "async",
    "await", "yield", "throw", "raise", "super", "this", "self", "import",
    "from", "package", "namespace", "using", "typeof", "instanceof", "sizeof",
    "make", "len", "append", "range", "in", "is", "and", "or", "not", "lambda",
    "require", "module", "export", "type", "enum", "impl", "fn", "match",
}

# Prompt de sistema estricto: el punto central del pilar "antialucinaciones".
# Se le prohíbe explícitamente usar conocimiento externo al contexto recuperado.
SYSTEM_PROMPT = """Eres un asistente técnico que ayuda a desarrolladores a entender un repositorio de código específico.

REGLAS ESTRICTAS:
1. Responde ÚNICAMENTE usando la información de los fragmentos de código que se te entregan a continuación como CONTEXTO.
2. Si la respuesta no está en el CONTEXTO, di explícitamente: "No encontré información suficiente en el código indexado para responder esto." No inventes ni asumas comportamiento que no esté en el contexto.
3. Cuando cites código, referencia el archivo y las líneas exactas que se te dieron (ej: "en utils.py, líneas 10-25").
4. No sugieras soluciones genéricas de buenas prácticas si no están respaldadas por el CONTEXTO — el desarrollador quiere saber qué hace SU código, no una recomendación general.
5. Sé directo y técnico. No agregues disculpas ni relleno conversacional innecesario.
6. CASO ESPECÍFICO — llamadas a funciones no incluidas en el CONTEXTO: si el código que ves LLAMA a otra función/método (ej: "return CalcularAlgo(x)") pero el CUERPO de esa función NO aparece en ningún fragmento del CONTEXTO, NUNCA inventes ni supongas qué hace esa función (nada de "probablemente calcula X multiplicando Y", "seguramente aplica Z"). En vez de eso, di explícitamente algo como: "El código llama a la función `CalcularAlgo`, pero su implementación no está entre los fragmentos recuperados, así que no puedo confirmar exactamente qué hace." Esta regla es más importante que sonar completo o útil — una respuesta honestamente incompleta es siempre preferible a una que rellena huecos con suposiciones.
7. Algunos fragmentos del CONTEXTO están marcados como "[incluido automáticamente porque es llamado desde otro fragmento]" — significa que NO vinieron de la búsqueda semántica normal, sino que el sistema detectó que otro fragmento los invoca y los agregó para completar la cadena de llamadas. Trátalos con la misma validez que el resto: si resuelven la pregunta, úsalos con confianza.
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
    expanded: bool = False  # True si se agregó por grafo de llamadas, no por búsqueda semántica

    def as_context_block(self, index: int) -> str:
        location = f"{self.file_path} (líneas {self.start_line}-{self.end_line})"
        label = f"{self.chunk_type} '{self.name}'" if self.name else self.chunk_type
        tag = " [incluido automáticamente porque es llamado desde otro fragmento]" if self.expanded else ""
        return f"[Fragmento {index}] {label} — {location}{tag}\n```\n{self.code}\n```"


@dataclass
class RAGResponse:
    answer: str
    sources: List[RetrievedChunk]

    def to_dict(self) -> dict:
        return {
            "answer": self.answer,
            "sources": [
                {
                    "file_path": s.file_path,
                    "chunk_type": s.chunk_type,
                    "name": s.name,
                    "start_line": s.start_line,
                    "end_line": s.end_line,
                    "distance": round(s.distance, 4),
                    "expanded": s.expanded,
                }
                for s in self.sources
            ],
        }


class RAGEngine:
    def __init__(self):
        self.client = chromadb.PersistentClient(path=CHROMA_DIR)
        self.collection = self.client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )

    def _embed_query(self, text: str) -> List[float]:
        # Debe usar "search_query:", NO "search_document:" — nomic-embed-text
        # entrena representaciones distintas según el rol (pregunta vs.
        # contenido indexado). Usar el prefijo equivocado, o ninguno, degrada
        # la búsqueda aunque el texto sea idéntico.
        response = ollama.embeddings(model=EMBEDDING_MODEL, prompt=f"search_query: {text}")
        return response["embedding"]

    def retrieve(self, question: str, top_k: int = DEFAULT_TOP_K) -> List[RetrievedChunk]:
        if self.collection.count() == 0:
            return []

        query_embedding = self._embed_query(question)
        results = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=min(top_k, self.collection.count()),
        )

        chunks = []
        ids = results["ids"][0]
        docs = results["documents"][0]
        metas = results["metadatas"][0]
        dists = results["distances"][0]

        for doc, meta, dist in zip(docs, metas, dists):
            chunks.append(RetrievedChunk(
                file_path=meta.get("file_path", "?"),
                chunk_type=meta.get("chunk_type", "raw"),
                name=meta.get("name", ""),
                start_line=meta.get("start_line", 0),
                end_line=meta.get("end_line", 0),
                code=doc,
                distance=dist,
            ))
        return chunks

    def _build_prompt(self, question: str, chunks: List[RetrievedChunk]) -> str:
        if not chunks:
            context = "(No se encontraron fragmentos relevantes en el índice.)"
        else:
            context = "\n\n".join(c.as_context_block(i + 1) for i, c in enumerate(chunks))

        return (
            f"CONTEXTO (fragmentos recuperados del repositorio):\n\n{context}\n\n"
            f"---\n\nPREGUNTA DEL DESARROLLADOR: {question}\n\n"
            f"Responde siguiendo estrictamente las reglas del sistema."
        )

    @staticmethod
    def _extract_call_candidates(code: str, own_name: str) -> List[str]:
        """Identificadores que parecen ser llamadas a función dentro de 'code',
        excluyendo palabras reservadas y el propio nombre del chunk (para no
        'auto-expandir' una función recursiva sobre sí misma)."""
        candidates, seen = [], set()
        for match in CALL_CANDIDATE_PATTERN.finditer(code):
            name = match.group(1)
            if name in KEYWORD_BLOCKLIST or name == own_name or name in seen or len(name) <= 2:
                continue
            seen.add(name)
            candidates.append(name)
        return candidates

    def _find_chunk_by_name(self, name: str, caller_file_path: str) -> Optional[RetrievedChunk]:
        """
        Busca un chunk indexado por nombre exacto. Si hay varias coincidencias
        (nombres de función repetidos en distintos servicios, ej: 'Check' de
        health-check aparece en 3 microservicios distintos), se prioriza la
        que esté en el mismo 'scope' (primeros 2 segmentos de ruta, ej.
        'src/shippingservice') que quien hace la llamada. Si sigue ambiguo
        entre servicios distintos y ninguno comparte scope, se omite — mejor
        no expandir que expandir con la función equivocada.
        """
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
            if len(same_scope) == 1:
                doc, meta = same_scope[0]
            else:
                return None  # ambiguo entre servicios distintos: no arriesgamos

        return RetrievedChunk(
            file_path=meta.get("file_path", "?"),
            chunk_type=meta.get("chunk_type", "raw"),
            name=meta.get("name", ""),
            start_line=meta.get("start_line", 0),
            end_line=meta.get("end_line", 0),
            code=doc,
            distance=-1.0,  # no aplica: no vino de ranking semántico
            expanded=True,
        )

    def expand_with_call_graph(self, chunks: List[RetrievedChunk],
                                max_expansions: int = DEFAULT_MAX_CALL_EXPANSIONS) -> List[RetrievedChunk]:
        """Para cada chunk función/método recuperado, detecta a qué otras
        funciones llama y, si están indexadas, las agrega como contexto
        adicional — aunque no hayan rankeado bien semánticamente. Este es el
        caso 'GetQuote llama a CreateQuoteFromCount, pero esta última no
        aparecía en el top-k por similitud semántica'."""
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

    def ask(self, question: str, top_k: int = DEFAULT_TOP_K, expand_call_graph: bool = True) -> RAGResponse:
        chunks = self.retrieve(question, top_k=top_k)

        if expand_call_graph:
            chunks = chunks + self.expand_with_call_graph(chunks)

        prompt = self._build_prompt(question, chunks)

        response = ollama.chat(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        )
        answer = response["message"]["content"]
        return RAGResponse(answer=answer, sources=chunks)


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Uso: python rag_engine.py \"tu pregunta sobre el código\"")
        sys.exit(1)

    question = " ".join(sys.argv[1:])
    engine = RAGEngine()
    result = engine.ask(question)

    print(f"\nPREGUNTA: {question}\n")
    print(f"RESPUESTA:\n{result.answer}\n")
    print(f"FUENTES ({len(result.sources)}):")
    for s in result.sources:
        tag = " [expandido por grafo de llamadas]" if s.expanded else ""
        dist = f"{s.distance:.4f}" if s.distance >= 0 else "n/a"
        print(f"  - {s.file_path} líneas {s.start_line}-{s.end_line} ({s.chunk_type} '{s.name}') "
              f"[distancia: {dist}]{tag}")
