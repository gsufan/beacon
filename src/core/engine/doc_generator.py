"""Incremental .md documentation generated from the already indexed chunks."""

from pathlib import Path
from typing import Callable, List, Optional

from core.engine.chroma_utils import get_chroma_client

from core.config import AIProviderConfig
from core.projects import ProjectContext
from core.engine.git_watcher import GitWatcher, ChangeSet
from core.engine.indexer import COLLECTION_NAME, CONTROL_COLLECTION_NAME, CONTROL_KEY_ID
from core.engine.overview import FOLDER_SYSTEM_PROMPT, OVERVIEW_MD, build_overview
from core.engine.text_sanitize import strip_preamble
from core.engine.llm import CHARS_PER_TOKEN, chat, estimate_tokens, prompt_budget_tokens

DOC_CONTROL_KEY_ID = "last_documented_commit"


class IndexOutOfDateError(RuntimeError):
    """Documentation is built from the indexed chunks: with a stale index it would document old code and mark it as up to date."""


ProgressCallback = Optional[Callable[[int, int, str], None]]

# Without this instruction the model tends to answer in English when the code and its comments are in English.
DOC_LANGUAGE = ("IMPORTANTE: escribe todo en español neutro (sin modismos regionales ni voseo), "
                "aunque el código o sus comentarios estén en inglés. Mantén en su forma original "
                "los nombres de funciones, clases y variables.")

DOC_ANSWER_RESERVE_TOKENS = 1536
# Cap on note-condensation rounds (huge files): about 6,000 tokens per round is enough for files of several MB.
MAX_REDUCE_ROUNDS = 4

NOTES_SYSTEM_PROMPT = """Eres un ingeniero de software senior. Recibes UNA PARTE de un archivo de código más grande (o notas previas sobre él).
Escribe notas breves en Markdown, sin introducción, con exactamente estas listas:

Componentes:
- `nombre` — qué hace (una línea por función/clase/método de esta parte).
Dependencias:
- qué importa o de qué módulos/servicios depende esta parte.
Riesgos:
- solo riesgos con evidencia concreta (nombre de función/variable y qué hace). Si no hay, escribe "ninguno".

Basa todo en el texto dado; no inventes componentes que no aparecen.

""" + DOC_LANGUAGE

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

""" + DOC_LANGUAGE


class DocGenerator:
    def __init__(self, project: ProjectContext, ai_config: AIProviderConfig):
        self.project = project
        self.ai_config = ai_config
        self.watcher = GitWatcher(project.repo_path)
        self.backend = ai_config.backend(Path(project.chroma_dir).parent)
        self.client = get_chroma_client(project.chroma_dir)
        self.control = self.client.get_or_create_collection(name=CONTROL_COLLECTION_NAME)

    @property
    def collection(self):
        # Looked up by name on each use: an index rebuild recreates the collection and a stored reference would
        # point to the deleted one.
        return self.client.get_or_create_collection(name=COLLECTION_NAME, metadata={"hnsw:space": "cosine"})

    def _get_control_commit(self, key: str):
        result = self.control.get(ids=[key])
        return result["metadatas"][0]["commit_hash"] if result["ids"] else None

    def _get_last_documented_commit(self):
        return self._get_control_commit(DOC_CONTROL_KEY_ID)

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

    @staticmethod
    def _chunk_block(c: dict) -> str:
        label = f"{c.get('chunk_type','raw')} '{c.get('name','')}'" if c.get("name") else c.get("chunk_type", "raw")
        return f"--- {label} (líneas {c.get('start_line')}-{c.get('end_line')}) ---\n{c['code']}\n"

    def _content_budget_chars(self, system_prompt: str) -> int:
        """Characters of content that fit in a prompt, after the system prompt, the header and the reserve for the answer."""
        tokens = prompt_budget_tokens(DOC_ANSWER_RESERVE_TOKENS, self.ai_config.llm_num_ctx) - estimate_tokens(system_prompt) - 100
        return int(tokens * CHARS_PER_TOKEN)

    @staticmethod
    def _group(blocks: List[str], budget_chars: int) -> List[List[str]]:
        """Groups consecutive blocks without exceeding the budget. A block that does not fit on its own is
        trimmed (it should not happen: the indexer already splits long chunks) and the trim is marked.
        """
        groups, current, size = [], [], 0
        for block in blocks:
            if len(block) > budget_chars:
                block = block[:budget_chars - 60] + "\n[... fragmento recortado por longitud ...]\n"
            if current and size + len(block) > budget_chars:
                groups.append(current)
                current, size = [], 0
            current.append(block)
            size += len(block)
        if current:
            groups.append(current)
        return groups

    def _llm(self, system: str, user: str) -> str:
        return chat(self.backend, self.ai_config.llm_model, system, user, self.ai_config.llm_num_ctx,
                    max_tokens=DOC_ANSWER_RESERVE_TOKENS).content

    def generate_doc_for_file(self, file_path: str) -> Optional[str]:
        """Documents the whole file. If it does not fit in one call, it is analyzed in parts (notes per part,
        condensed if needed) and the final document is written from those notes plus the complete component
        index, instead of documenting only the beginning.
        """
        chunks = self._get_chunks_for_file(file_path)
        if not chunks:
            return None
        blocks = [self._chunk_block(c) for c in chunks]
        budget = self._content_budget_chars(DOC_SYSTEM_PROMPT)
        # The index comes from the syntax analysis, not from the model: it is exact and complete even if the
        # model only summarizes the main components.
        index = self._components_index(chunks)
        appendix = f"\n\n## Índice de componentes\n\n{index}"

        if sum(len(b) for b in blocks) <= budget:
            body = self._llm(DOC_SYSTEM_PROMPT, f"Archivo: {file_path}\n\n{''.join(blocks)}\n\nEscribe la documentación pedida.")
            body = strip_preamble(body)
            return f"# `{file_path}`\n\n> Documentación generada automáticamente.\n\n{body}{appendix}"

        parts = self._group(blocks, self._content_budget_chars(NOTES_SYSTEM_PROMPT))
        notes = [self._llm(NOTES_SYSTEM_PROMPT, f"Archivo: {file_path} — parte {i} de {len(parts)}\n\n{''.join(g)}")
                 for i, g in enumerate(parts, 1)]
        prompt_index = index if len(index) <= budget // 3 else index[:budget // 3] + "\n- [... índice recortado en el prompt ...]\n"
        final_budget = budget - len(prompt_index)
        rounds = 0
        while sum(len(n) for n in notes) > final_budget and len(notes) > 1 and rounds < MAX_REDUCE_ROUNDS:
            groups = self._group([n + "\n" for n in notes], self._content_budget_chars(NOTES_SYSTEM_PROMPT))
            notes = [self._llm(NOTES_SYSTEM_PROMPT, f"Archivo: {file_path}. Condensa estas notas de varias partes:\n\n{''.join(g)}")
                     for g in groups]
            rounds += 1
        joined = "\n\n".join(f"### Notas de la parte {i}\n{n}" for i, n in enumerate(notes, 1))
        if len(joined) > final_budget:  # round cap reached: trimmed and declared
            joined = joined[:final_budget - 80] + "\n\n[... notas recortadas: archivo excepcionalmente extenso ...]"
        body = self._llm(
            DOC_SYSTEM_PROMPT,
            f"Archivo: {file_path}\n\nEl archivo es extenso y se analizó en {len(parts)} partes. "
            f"Índice de sus componentes:\n{prompt_index}\nNotas del análisis por partes:\n\n{joined}\n\n"
            "Escribe la documentación pedida para el archivo completo, basándote en el índice y las notas.",
        )
        body = strip_preamble(body)
        header = (f"# `{file_path}`\n\n> Documentación generada automáticamente. Archivo extenso: "
                  f"se analizó completo en {len(parts)} partes y se sintetizó.\n\n")
        return header + body + appendix

    @staticmethod
    def _components_index(chunks: List[dict]) -> str:
        lines, seen_splits = [], set()
        for c in chunks:
            # A long chunk is indexed only as its parts (with split_of and the original lines): it is listed once,
            # not once per part.
            if c.get("split_of"):
                if c["split_of"] in seen_splits:
                    continue
                seen_splits.add(c["split_of"])
            name = c.get("name")
            label = "código a nivel de módulo" if not name or name == "module_level" else f"`{name}`"
            lines.append(f"- {label} — {c.get('chunk_type', 'raw')}, líneas {c.get('start_line')}-{c.get('end_line')}")
        return "\n".join(lines) + "\n"

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

    def _existing_doc_sources(self) -> List[str]:
        docs_dir = Path(self.project.docs_dir)
        if not docs_dir.exists():
            return []
        return [p.relative_to(docs_dir).as_posix()[: -len(".md")] for p in docs_dir.rglob("*.md") if p.name != OVERVIEW_MD or p.parent != docs_dir]

    def update_overview(self, on_progress: ProgressCallback = None) -> dict:
        """Builds or updates the repository overview from the per-file documentation."""
        step = (lambda folder: on_progress(1, 1, "overview: " + (folder or "."))) if on_progress else None
        return build_overview(Path(self.project.docs_dir), self._llm, self._content_budget_chars(FOLDER_SYSTEM_PROMPT), step)

    def stats(self) -> dict:
        return {
            "ultimo_commit_documentado": self._get_last_documented_commit(),
            "commit_actual": self.watcher.current_commit_hash(),
        }

    def sync(self, on_progress: ProgressCallback = None, full: bool = False) -> dict:
        head = self.watcher.current_commit_hash()  # fixed for the whole sync
        if self._get_control_commit(CONTROL_KEY_ID) != head:
            raise IndexOutOfDateError(
                "El índice no está al día con el último commit. Corre 'beacon sync' antes de generar la documentación."
            )

        last_commit = None if full else self._get_last_documented_commit()
        changes: ChangeSet = self.watcher.get_changes_since(last_commit, target=head)
        if changes.is_empty():
            result = {"status": "sin_cambios", "detalle": changes.summary()}
            if self._existing_doc_sources():  # no model calls if nothing changed; builds it for projects documented earlier
                result["resumen_proyecto"] = self.update_overview(on_progress)
            return result

        files = changes.files_to_reindex()
        deleted = 0
        stale = changes.files_to_purge()
        if changes.full_rescan:
            # without a base commit it is unknown what was deleted: any .md without a current source file is an orphan
            current = set(files)
            stale = [fp for fp in self._existing_doc_sources() if fp not in current]
        for fp in stale:
            self.delete_doc(fp)
            deleted += 1
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

        overview = self.update_overview(on_progress)
        self._save_last_documented_commit(head)
        return {
            "status": "ok", "detalle": changes.summary(),
            "docs_generados": generated, "docs_eliminados": deleted, "sin_chunks_indexados": skipped,
            "resumen_proyecto": overview,
        }
