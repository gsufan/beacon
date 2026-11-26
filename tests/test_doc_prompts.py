"""Documentation prompts: their examples must not contain identifiers the model could copy into a document."""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.engine.doc_generator import DOC_SYSTEM_PROMPT, NOTES_SYSTEM_PROMPT  # noqa: E402
from core.engine.overview import FOLDER_SYSTEM_PROMPT  # noqa: E402

IDENTIFIER = re.compile(r"\b(?:[a-z]+[A-Z]\w*|[A-Z][a-z]+[A-Z]\w*|[a-z]+_[a-z_]+)\b")


def test_prompts_have_no_concrete_identifiers():
    for prompt in (DOC_SYSTEM_PROMPT, NOTES_SYSTEM_PROMPT, FOLDER_SYSTEM_PROMPT):
        assert IDENTIFIER.findall(prompt) == []


def test_doc_prompt_limits_names_to_the_given_code():
    assert "Nombra únicamente funciones, clases y variables que aparecen en el código dado" in DOC_SYSTEM_PROMPT
