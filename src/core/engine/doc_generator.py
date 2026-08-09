"""Genera documentación .md incremental a partir de los chunks ya indexados."""

from pathlib import Path
from typing import Callable, List, Optional

from core.engine.chroma_utils import get_chroma_client
import ollama

from core.config import AIProviderConfig
from core.projects import ProjectContext
from core.engine.git_watcher import GitWatcher, ChangeSet
from core.engine.indexer import COLLECTION_NAME, CONTROL_COLLECTION_NAME

DOC_CONTROL_KEY_ID = "last_documented_commit"
MAX_PROMPT_CHARS = 20000
ProgressCallback = Optional[Callable[[int, int, str], None]]

DOC_SYSTEM_PROMPT = """Eres un ingeniero de software senior escribiendo documentación técnica para tu equipo.

Recibes el código de UN archivo, ya separado por función/clase. Escribe en Markdown:

## Propósito
2-4 líneas, concreto, no genérico.

## Componentes principales
Una línea por función/clase/método relevante: `nombre` — qué hace.

## Dependencias
Qué importa y de qué otros módulos/servicios depende.

## Riesgos de deuda técnica
Para CADA riesgo, cita evidencia específica (nombre de función/variable, qué hace exactamente).
Mal: "falta manejo de errores en algunos métodos".
Bien: "GetQuote llama a CreateQuoteFromCount(0) con un 0 fijo, ignorando el conteo real del carrito".
Si no hay riesgos con evidencia concreta: "No se identificaron riesgos específicos en los fragmentos analizados." No rellenes con categorías genéricas.

Basa todo en el código dado. Si es un mock/simulación, dilo explícitamente. Directo y técnico, sin relleno.
"""


class DocGenerator:
    def __init__(self, project: ProjectContext, ai_config: AIProviderConfig):
        self.project = project
        self.ai_config = ai_config
        self.watcher = GitWatcher(project.repo_path)
        self.client_ollama = ollama.Client(host=ai_config.ollama_host)
        self.client = get_chroma_client(project.chroma_dir)
        self.collection = self.client.get_or_create_collection(
            name=COLLECTION_NAME, metadata={"hnsw:space": "cosine"}
        )
        self.control = self.client.get_or_create_collection(name=CONTROL_COLLECTION_NAME)

    def _get_last_documented_commit(self):
        result = self.control.get(ids=[DOC_CONTROL_KEY_ID])
        return result["metadatas"][0]["commit_hash"] if result["ids"] else None

    def _save_last_documented_commit(self, commit_hash: str):
        self.control.upsert(
            ids=[DOC_CONTROL_KEY_ID], documents=[commit_hash],
            embeddings=[[0.0]], metadatas=[{"commit_hash": commit_hash}],
        )

    def _get_chunks_for_file(self, file_path: str) -> List[dict]:
        result = self.collection.get(where={"file_path": file_path}, include=["documents", "metadatas"])
        if not result["ids"]:
            return []
        items = [{"code": doc, **meta} for doc, meta in zip(result["documents"], result["metadatas"])]
        items.sort(key=lambda c: c.get("start_line", 0))
        return items

    def _build_prompt(self, file_path: str, chunks: List[dict]) -> str:
        pieces, total = [], 0
        for c in chunks:
            label = f"{c.get('chunk_type','raw')} '{c.get('name','')}'" if c.get("name") else c.get("chunk_type", "raw")
            block = f"--- {label} (líneas {c.get('start_line')}-{c.get('end_line')}) ---\n{c['code']}\n"
            if total + len(block) > MAX_PROMPT_CHARS:
                break
            pieces.append(block)
            total += len(block)
        return f"Archivo: {file_path}\n\n{''.join(pieces)}\n\nEscribe la documentación pedida."

    def generate_doc_for_file(self, file_path: str) -> Optional[str]:
        chunks = self._get_chunks_for_file(file_path)
        if not chunks:
            return None
        prompt = self._build_prompt(file_path, chunks)
        response = self.client_ollama.chat(
            model=self.ai_config.llm_model,
            messages=[{"role": "system", "content": DOC_SYSTEM_PROMPT}, {"role": "user", "content": prompt}],
        )
        header = f"# `{file_path}`\n\n> Documentación generada automáticamente.\n\n"
        return header + response["message"]["content"]

    def _doc_output_path(self, file_path: str) -> Path:
        return Path(self.project.docs_dir) / f"{file_path}.md"

    def write_doc(self, file_path: str, content: str):
        out = self._doc_output_path(file_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(content, encoding="utf-8")

    def delete_doc(self, file_path: str):
        out = self._doc_output_path(file_path)
        if out.exists():
            out.unlink()

    def stats(self) -> dict:
        return {
            "ultimo_commit_documentado": self._get_last_documented_commit(),
            "commit_actual": self.watcher.current_commit_hash(),
        }

    def sync(self, on_progress: ProgressCallback = None) -> dict:
        last_commit = self._get_last_documented_commit()
        changes: ChangeSet = self.watcher.get_changes_since(last_commit)
        if changes.is_empty():
            return {"status": "sin_cambios", "detalle": changes.summary()}

        deleted = 0
        for fp in changes.files_to_purge():
            self.delete_doc(fp)
            deleted += 1

        files = changes.files_to_reindex()
        total = len(files)
        generated, skipped = 0, []
        for i, fp in enumerate(files, 1):
            doc = self.generate_doc_for_file(fp)
            if doc is None:
                skipped.append(fp)
            else:
                self.write_doc(fp, doc)
                generated += 1
            if on_progress:
                on_progress(i, total, fp)

        self._save_last_documented_commit(self.watcher.current_commit_hash())
        return {
            "status": "ok", "detalle": changes.summary(),
            "docs_generados": generated, "docs_eliminados": deleted, "sin_chunks_indexados": skipped,
        }
