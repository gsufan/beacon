"""Tests de los criterios de tools/eval_answers.py (sin llamar al modelo)."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from core.engine.rag_engine import RetrievedChunk  # noqa: E402
import eval_answers  # noqa: E402

REFUSAL = "No encontré información suficiente en el código indexado para responder esto."


def _source(code, path="src/requests/sessions.py"):
    return RetrievedChunk(file_path=path, chunk_type="function", name="x", start_line=1, end_line=5,
                          code=code, distance=0.1)


def test_answerable_needs_every_group_and_the_file():
    item = {"q": "?", "must": [["merge_setting"], ["None", "nulo"]], "cites": "sessions.py"}
    sources = [_source("def merge_setting(a, b): ...")]

    good = eval_answers.grade(item, "Lo hace `merge_setting` en `src/requests/sessions.py`; quita las claves en None.",
                              sources)
    assert good["content_ok"] and good["cites_ok"] and not good["refused"]

    partial = eval_answers.grade(item, "Lo hace `merge_setting`.", sources)
    assert partial["missing"] == [["None", "nulo"]]
    assert not partial["cites_ok"]


def test_refusal_with_generic_code_is_not_a_clean_refusal():
    item = {"q": "¿Cómo se conecta a PostgreSQL?", "answerable": False}
    clean = eval_answers.grade(item, REFUSAL, [])
    padded = eval_answers.grade(item, REFUSAL + "\n\n```python\nimport psycopg2\n```", [])
    assert clean["clean_refusal"]
    assert padded["refused"] and not padded["clean_refusal"]


def test_flags_identifiers_that_are_not_in_the_context():
    sources = [_source("def rebuild_method(self, prepared_request, response): ...")]
    answer = "La función `rebuild_method` llama a `convert_to_get`."
    assert eval_answers.ungrounded_identifiers(answer, sources) == ["convert_to_get"]


def test_detects_language():
    assert eval_answers.is_spanish("La función cambia el método de la petición a GET.")
    assert not eval_answers.is_spanish("The function changes the method of the request to GET.")
