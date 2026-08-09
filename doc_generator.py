"""
Generador Automático de Documentación — Entregable 3 de la propuesta.

Por cada archivo del repositorio, toma los chunks YA INDEXADOS en ChromaDB
(mismo índice que usa rag_engine.py, con nombres/tipos/docstrings correctos
gracias al chunker políglota) y le pide al LLM local que redacte una
explicación técnica: propósito del módulo, funciones/clases clave,
dependencias y riesgos de deuda técnica.

Escribe un .md espejo de la estructura del repo en DOCS_OUTPUT_DIR, ej:
    src/shippingservice/quote.go  ->  docs/src/shippingservice/quote.go.md

Incremental vía git diff (igual que indexer.py): solo regenera documentación
de archivos que cambiaron desde la última corrida, y borra el .md de
archivos eliminados. Guarda su propio puntero de "último commit
documentado" (independiente del de indexación, pueden correr en momentos
distintos).
"""

import os
from pathlib import Path
from typing import List, Optional

import chromadb
import ollama
from dotenv import load_dotenv

from git_watcher import GitWatcher, ChangeSet

load_dotenv()

LLM_MODEL = os.getenv("LLM_MODEL", "llama3:8b")
CHROMA_DIR = os.getenv("CHROMA_PERSIST_DIR", "./data/chroma_db")
COLLECTION_NAME = os.getenv("CHROMA_COLLECTION_NAME", "codebase_index")
DOCS_OUTPUT_DIR = os.getenv("DOCS_OUTPUT_DIR", "./docs")

CONTROL_COLLECTION_NAME = "sys_index_control"
DOC_CONTROL_KEY_ID = "last_documented_commit"

# Límite de seguridad para no mandar un archivo gigante entero al LLM de una
# sola vez (mismo espíritu que el límite de embeddings, pero aquí es sobre
# el contexto del LLM de generación, no del modelo de embeddings).
MAX_PROMPT_CHARS = 20000

DOC_SYSTEM_PROMPT = """Eres un ingeniero de software senior escribiendo documentación técnica para otros desarrolladores del equipo.

Vas a recibir el código fuente de UN archivo (ya organizado por función/clase). Escribe documentación en Markdown con esta estructura exacta:

## Propósito
Qué hace este módulo/archivo en 2-4 líneas. Sé concreto, no genérico.

## Componentes principales
Para cada función/clase/método relevante, una línea: `nombre` — qué hace (basado en el código, no en suposiciones).

## Dependencias
Qué importa este archivo y de qué otros módulos/servicios depende (basado en imports y llamadas visibles en el código).

## Riesgos de deuda técnica
Problemas concretos que veas en ESTE código. Para CADA riesgo que menciones, cita la evidencia específica que lo respalda (el nombre de la función/variable y qué hace exactamente, no una categoría genérica). Ejemplos de la diferencia:
- MAL (genérico, no sirve): "Falta manejo de errores en algunos métodos."
- BIEN (específico, con evidencia): "`GetQuote` llama a `CreateQuoteFromCount(0)` con un `0` fijo en vez del conteo real de items del carrito — el costo de envío no varía según lo que se está enviando."
Si revisas el código y genuinamente no encuentras riesgos con evidencia concreta que citar, escribe: "No se identificaron riesgos específicos en los fragmentos analizados." NO rellenes con categorías genéricas (acoplamiento fuerte, falta de comentarios, etc.) solo para tener contenido en esta sección — una lista corta y específica vale más que una larga y genérica.

REGLAS:
- Basa TODO en el código que se te entrega. No inventes funcionalidad que no esté ahí.
- Si el código es claramente un mock/simulación (valores fijos donde se esperaría cálculo real), dilo explícitamente — es información valiosa para el equipo.
- Sé directo y técnico, sin relleno conversacional.
"""


class DocGenerator:
    def __init__(self, repo_path: str):
        self.repo_path = repo_path
        self.watcher = GitWatcher(repo_path)
        self.client = chromadb.PersistentClient(path=CHROMA_DIR)
        self.collection = self.client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
        self.control = self.client.get_or_create_collection(name=CONTROL_COLLECTION_NAME)

    # ---------- Estado de documentación (independiente del de indexación) ----------

    def _get_last_documented_commit(self) -> Optional[str]:
        result = self.control.get(ids=[DOC_CONTROL_KEY_ID])
        if result["ids"]:
            return result["metadatas"][0]["commit_hash"]
        return None

    def _save_last_documented_commit(self, commit_hash: str):
        self.control.upsert(
            ids=[DOC_CONTROL_KEY_ID],
            documents=[commit_hash],
            embeddings=[[0.0]],  # dummy: evita que Chroma intente descargar su modelo por defecto
            metadatas=[{"commit_hash": commit_hash}],
        )

    # ---------- Lectura de chunks ya indexados ----------

    def _get_chunks_for_file(self, file_path: str) -> List[dict]:
        result = self.collection.get(where={"file_path": file_path}, include=["documents", "metadatas"])
        if not result["ids"]:
            return []
        items = [
            {"code": doc, **meta}
            for doc, meta in zip(result["documents"], result["metadatas"])
        ]
        # Orden por línea, así el LLM lee el archivo en el mismo orden que un humano
        items.sort(key=lambda c: c.get("start_line", 0))
        return items

    def _build_prompt(self, file_path: str, chunks: List[dict]) -> str:
        pieces = []
        total_chars = 0
        truncated = False
        for c in chunks:
            label = f"{c.get('chunk_type', 'raw')} '{c.get('name', '')}'" if c.get("name") else c.get("chunk_type", "raw")
            block = f"--- {label} (líneas {c.get('start_line')}-{c.get('end_line')}) ---\n{c['code']}\n"
            if total_chars + len(block) > MAX_PROMPT_CHARS:
                truncated = True
                break
            pieces.append(block)
            total_chars += len(block)

        code_context = "\n".join(pieces)
        note = ("\n\n(NOTA: el archivo es más largo que lo mostrado arriba; esto es una porción "
                "representativa, no el archivo completo.)") if truncated else ""

        return f"Archivo: {file_path}\n\n{code_context}{note}\n\nEscribe la documentación siguiendo la estructura pedida."

    # ---------- Generación ----------

    def generate_doc_for_file(self, file_path: str) -> Optional[str]:
        chunks = self._get_chunks_for_file(file_path)
        if not chunks:
            return None

        prompt = self._build_prompt(file_path, chunks)
        response = ollama.chat(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": DOC_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        )
        body = response["message"]["content"]
        header = f"# `{file_path}`\n\n> Documentación generada automáticamente. Última actualización basada en el commit indexado más reciente.\n\n"
        return header + body

    def _doc_output_path(self, file_path: str) -> Path:
        return Path(DOCS_OUTPUT_DIR) / f"{file_path}.md"

    def write_doc(self, file_path: str, content: str):
        out_path = self._doc_output_path(file_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(content, encoding="utf-8")

    def delete_doc(self, file_path: str):
        out_path = self._doc_output_path(file_path)
        if out_path.exists():
            out_path.unlink()

    # ---------- Orquestación incremental ----------

    def sync(self) -> dict:
        last_commit = self._get_last_documented_commit()
        changes: ChangeSet = self.watcher.get_changes_since(last_commit)

        if changes.is_empty():
            return {"status": "sin_cambios", "detalle": changes.summary()}

        deleted_count = 0
        for file_path in changes.files_to_purge():
            self.delete_doc(file_path)
            deleted_count += 1

        generated_count = 0
        skipped_no_chunks = []
        for file_path in changes.files_to_reindex():
            doc = self.generate_doc_for_file(file_path)
            if doc is None:
                skipped_no_chunks.append(file_path)
                continue
            self.write_doc(file_path, doc)
            generated_count += 1

        self._save_last_documented_commit(self.watcher.current_commit_hash())

        return {
            "status": "ok",
            "detalle": changes.summary(),
            "docs_generados": generated_count,
            "docs_eliminados": deleted_count,
            "sin_chunks_indexados": skipped_no_chunks,  # archivos que cambiaron pero no están en el índice (¿falta correr indexer.py?)
        }


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Uso: python doc_generator.py <ruta_repo>")
        sys.exit(1)

    repo_path = sys.argv[1]
    generator = DocGenerator(repo_path)
    print("Generando documentación...")
    result = generator.sync()
    print(result)
