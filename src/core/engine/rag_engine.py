"""RAG engine: coordinates retrieval, call-graph expansion, the prompt and the local LLM."""

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

from core.config import AIProviderConfig
from core.engine import prompting
from core.engine.backends import Backend, create_backend
from core.engine.call_graph import DEFAULT_MAX_CALL_EXPANSIONS, CallGraphExpander
from core.engine.chroma_utils import get_chroma_client
from core.engine.embeddings import embed_query
from core.engine.index_store import IndexControl
from core.engine.indexer import IndexModelMismatchError
from core.engine.llm import CHARS_PER_TOKEN, chat
from core.engine.overview import RepoOverview
from core.engine.priority import priority
from core.engine.question_kind import is_general_question
from core.engine.retrieval import DEFAULT_TOP_K, RetrievedChunk, Retriever, question_identifiers
from core.engine.text_sanitize import strip_preamble
from core.projects import ProjectContext

# Open questions about the whole repository are answered from the repository overview (see overview.py)
# plus a few code chunks. The overview takes at most this share of the prompt budget.
OVERVIEW_BUDGET_SHARE = 0.55
GENERAL_TOP_K = 3


@dataclass
class RAGResponse:
    answer: str
    sources: List[RetrievedChunk]
    # Tokens the server reports having processed (0 if not reported); tools/eval_answers.py uses them to
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
    def __init__(self, project: ProjectContext, ai_config: AIProviderConfig, *, backend: Optional[Backend] = None,
                 client=None):
        """`backend` and `client` default to the ones of the configuration and the project."""
        self.project = project
        self.ai_config = ai_config
        self.backend = backend or create_backend(ai_config.provider, ai_config.host)
        self.client = client or get_chroma_client(project.chroma_dir)
        self.retriever = Retriever(self.client, lambda text: embed_query(self.backend, self.ai_config.embedding_model, text))
        self.call_graph = CallGraphExpander(lambda name: self.retriever.chunks_named(name))
        self.index_model = IndexControl(self.client).embedding_model(self.collection)

    @property
    def collection(self):
        return self.retriever.collection

    @collection.setter
    def collection(self, value):
        self.retriever.collection = value

    def _check_index_model(self):
        if self.index_model and self.index_model != self.ai_config.embedding_model:
            raise IndexModelMismatchError(
                f"El índice de '{self.project.id}' se construyó con '{self.index_model}' y la configuración usa "
                f"'{self.ai_config.embedding_model}'. Sincroniza el proyecto para reconstruirlo (beacon sync {self.project.id})."
            )

    def ranked_candidates(self, question: str, n: int) -> List[RetrievedChunk]:
        self._check_index_model()
        return self.retriever.ranked_candidates(question, n)

    def retrieve(self, question: str, top_k: int = DEFAULT_TOP_K) -> List[RetrievedChunk]:
        self._check_index_model()
        return self.retriever.retrieve(question, top_k)

    def select_context(self, question: str, top_k: int = DEFAULT_TOP_K) -> List[RetrievedChunk]:
        self._check_index_model()
        return self.retriever.select_context(question, top_k)

    def expand_with_call_graph(self, chunks: List[RetrievedChunk],
                               max_expansions: int = DEFAULT_MAX_CALL_EXPANSIONS) -> List[RetrievedChunk]:
        return self.call_graph.expand(chunks, max_expansions)

    def overview_chunks(self, max_tokens: int) -> List[RetrievedChunk]:
        """The repository overview as context blocks (empty if the project has none yet)."""
        overview = RepoOverview(Path(self.project.docs_dir))
        return [RetrievedChunk(file_path=label, chunk_type="overview", name="", start_line=0, end_line=0, code=text, distance=-1.0)
                for label, text in overview.blocks(int(max_tokens * CHARS_PER_TOKEN))]

    def _context(self, question: str, top_k: int, expand_call_graph: bool) -> tuple:
        """Chunks for the prompt and whether they answer an open question from the repository overview."""
        overview = []
        if is_general_question(question, question_identifiers(question)):
            window = prompting.context_budget_tokens(self.ai_config.llm_num_ctx)
            overview = self.overview_chunks(int(window * OVERVIEW_BUDGET_SHARE))
        if overview:
            return overview + self.select_context(question, top_k=GENERAL_TOP_K), True
        chunks = self.select_context(question, top_k=top_k)
        if expand_call_graph:
            chunks = chunks + self.expand_with_call_graph(chunks)
        return chunks, False

    def ask(
        self, question: str, top_k: int = DEFAULT_TOP_K, expand_call_graph: bool = True, language: str = "es",
        max_seconds: Optional[float] = None, should_stop: Optional[Callable[[], bool]] = None,
    ) -> RAGResponse:
        with priority.query():  # background documentation yields the model server meanwhile
            return self._ask(question, top_k, expand_call_graph, language, max_seconds, should_stop)

    def _ask(self, question, top_k, expand_call_graph, language, max_seconds, should_stop) -> RAGResponse:
        chunks, general = self._context(question, top_k, expand_call_graph)
        system = prompting.system_prompt(language, general)
        chunks = prompting.fit_to_budget(question, system, chunks, self.ai_config.llm_num_ctx)
        result = chat(self.backend, self.ai_config.llm_model, system, prompting.build_prompt(question, chunks),
                      self.ai_config.llm_num_ctx, max_tokens=prompting.ANSWER_RESERVE_TOKENS,
                      max_seconds=max_seconds, should_stop=should_stop)
        answer = strip_preamble(result.content, heading_marker="\x00")
        if result.truncated:
            answer += prompting.truncated_note(language)
        return RAGResponse(answer=answer, sources=chunks,
                           prompt_tokens=result.prompt_tokens, answer_tokens=result.answer_tokens)
