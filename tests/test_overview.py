"""Repository overview: summaries built from the per-file documentation, and open-question routing."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import git  # noqa: E402
import pytest  # noqa: E402

from core.config import AIProviderConfig  # noqa: E402
from core.engine.backends import OllamaBackend  # noqa: E402
from core.engine.doc_generator import DocGenerator  # noqa: E402
from core.engine.indexer import CodebaseIndexer  # noqa: E402
from core.engine.overview import OVERVIEW_MD, RepoOverview, build_overview, extract_purpose  # noqa: E402
from core.engine.question_kind import is_general_question  # noqa: E402
from core.engine.rag_engine import RAGEngine  # noqa: E402
from core.projects import ProjectContext  # noqa: E402


def _doc(purpose):
    return f"# `x`\n\n> Documentación generada automáticamente.\n\n## Propósito\n{purpose}\n\n## Componentes principales\n- `f` — hace algo\n"


def _write_docs(docs_dir, files):
    for path, purpose in files.items():
        p = docs_dir / f"{path}.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(_doc(purpose), encoding="utf-8")


class _Llm:
    def __init__(self):
        self.calls = []

    def __call__(self, system, user):
        self.calls.append(user)
        return "Resumen de prueba."


FILES = {"src/cart/main.py": "Maneja el carrito.", "src/cart/store.py": "Guarda los items.", "src/pay/main.py": "Cobra los pagos.", "README": "Presentación."}


def test_extract_purpose_reads_the_purpose_section_only():
    assert extract_purpose(_doc("Hace   esto\ny aquello.")) == "Hace esto y aquello."
    assert extract_purpose("# t\n\nsolo texto") == "solo texto"


def test_build_overview_summarizes_folders_and_the_project(tmp_path):
    _write_docs(tmp_path, FILES)
    llm = _Llm()
    stats = build_overview(tmp_path, llm, budget_chars=20000)
    # one call per folder with several entries (src/cart, src) plus the project; src/pay has a single file and uses its purpose
    assert stats["carpetas_resumidas"] == len(llm.calls) and stats["carpetas_reutilizadas"] == 0
    md = (tmp_path / OVERVIEW_MD).read_text(encoding="utf-8")
    assert "# Resumen del proyecto" in md and "### `src/cart/`" in md and "Cobra los pagos." in md
    overview = RepoOverview(tmp_path)
    labels = [label for label, _ in overview.blocks(10_000)]
    assert labels[0] == "resumen del proyecto" and "resumen de la carpeta src/cart" in labels


def test_overview_is_incremental(tmp_path):
    _write_docs(tmp_path, FILES)
    build_overview(tmp_path, _Llm(), budget_chars=20000)
    llm = _Llm()
    stats = build_overview(tmp_path, llm, budget_chars=20000)
    assert llm.calls == [] and stats["carpetas_resumidas"] == 0
    _write_docs(tmp_path, {"src/cart/store.py": "Ahora guarda los items en disco."})
    llm = _Llm()
    build_overview(tmp_path, llm, budget_chars=20000)
    folders = [c.splitlines()[0] for c in llm.calls]
    assert "Carpeta: src/cart" in folders and "Carpeta: src/pay" not in folders


def test_overview_without_docs_leaves_nothing_behind(tmp_path):
    assert build_overview(tmp_path, _Llm(), budget_chars=1000) == {"carpetas_resumidas": 0, "carpetas_reutilizadas": 0}
    assert not RepoOverview(tmp_path).exists and RepoOverview(tmp_path).blocks(1000) == []


def test_long_folders_are_shortened_to_fit_the_prompt(tmp_path):
    _write_docs(tmp_path, {f"lib/m{i}.py": "x" * 400 for i in range(40)})
    llm = _Llm()
    build_overview(tmp_path, llm, budget_chars=3000)
    assert all(len(c) <= 3000 for c in llm.calls)


@pytest.mark.parametrize("question", [
    "¿Qué hace este proyecto?", "que hace el repositorio", "¿Cómo está organizado el código?", "Dame un resumen general del proyecto",
    "¿Cuáles son los principales componentes?", "¿Qué servicios hay?", "What does this project do?", "How is the project structured?",
    "Give me a high-level overview", "¿Qué tecnologías usa?",
])
def test_general_questions_are_recognized(question):
    assert is_general_question(question)


@pytest.mark.parametrize("question", [
    "¿Cómo se calcula el total de una orden?", "¿Dónde se usa max_redirects?", "How does the retry logic work?", "¿Qué hace la función merge_setting?",
])
def test_specific_questions_are_not_general(question):
    assert not is_general_question(question)


def test_a_question_naming_an_identifier_is_about_code():
    assert not is_general_question("¿Qué hace este proyecto con super_len?", identifiers=["super_len"])


# ---- integration with the RAG engine and the documentation generator ----

class _Embed:
    def embed(self, model, input, **kwargs):
        return {"embeddings": [[0.1, 0.2, 0.3] for _ in input]}

    def chat(self, **kwargs):
        self.last = kwargs
        return {"message": {"content": "respuesta"}, "prompt_eval_count": 10, "eval_count": 3}


def _project(tmp_path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    repo = git.Repo.init(repo_dir)
    (repo_dir / "a.py").write_text("def hola():\n    return 1\n")
    repo.index.add(["a.py"])
    repo.index.commit("init")
    ctx = ProjectContext(id="t", name="t", repo_path=str(repo_dir), chroma_dir=str(tmp_path / "chroma"), docs_dir=str(tmp_path / "docs"))
    ai = AIProviderConfig(provider="ollama", host="http://127.0.0.1:9", embedding_model="qwen3-embedding:0.6b", llm_model="l")
    return ctx, ai


def test_general_questions_use_the_overview_and_specific_ones_do_not(tmp_path):
    ctx, ai = _project(tmp_path)
    idx = CodebaseIndexer(ctx, ai)
    idx.backend = OllamaBackend(_Embed())
    idx.sync()
    _write_docs(Path(ctx.docs_dir), FILES)
    build_overview(Path(ctx.docs_dir), _Llm(), budget_chars=20000)
    fake = _Embed()
    engine = RAGEngine(ctx, ai)
    engine.backend = OllamaBackend(fake)

    general = engine.ask("¿Qué hace este proyecto?")
    assert general.sources[0].chunk_type == "overview" and general.sources[0].file_path == "resumen del proyecto"
    assert "[resumen del proyecto]" in fake.last["messages"][1]["content"]
    assert any(s.chunk_type != "overview" for s in general.sources)  # a few code chunks stay as grounding

    specific = engine.ask("¿Qué hace la función hola?")
    assert all(s.chunk_type != "overview" for s in specific.sources)


def test_general_question_without_an_overview_falls_back_to_retrieval(tmp_path):
    ctx, ai = _project(tmp_path)
    idx = CodebaseIndexer(ctx, ai)
    idx.backend = OllamaBackend(_Embed())
    idx.sync()
    engine = RAGEngine(ctx, ai)
    engine.backend = OllamaBackend(_Embed())
    assert all(s.chunk_type != "overview" for s in engine.ask("¿Qué hace este proyecto?").sources)


def test_overview_file_is_not_treated_as_an_orphan_document(tmp_path):
    ctx, ai = _project(tmp_path)
    gen = DocGenerator(ctx, ai)
    docs = Path(ctx.docs_dir)
    _write_docs(docs, {"a.py": "Hace algo."})
    build_overview(docs, _Llm(), budget_chars=5000)
    assert (docs / OVERVIEW_MD).exists()
    assert gen._existing_doc_sources() == ["a.py"]
