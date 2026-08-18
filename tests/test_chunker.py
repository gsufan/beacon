"""Tests del chunker políglota (src/core/engine/chunker.py)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))  # noqa: E402

import pytest  # noqa: E402

from core.engine.chunker import (  # noqa: E402
    chunk_fallback,
    chunk_file,
    chunk_with_treesitter,
    detect_language,
)


# --- detect_language -------------------------------------------------------

@pytest.mark.parametrize("path,expected", [
    ("app.py", "python"),
    ("src/main.go", "go"),
    ("Service.java", "java"),
    ("Component.tsx", "tsx"),
    ("lib.rs", "rust"),
    ("index.PHP", "php"),  # extensión en mayúsculas también debe reconocerse
])
def test_detect_language_known_extensions(path, expected):
    assert detect_language(path) == expected


def test_detect_language_unknown_extension_returns_none():
    assert detect_language("README.md") is None
    assert detect_language("data.json") is None


# --- chunk_with_treesitter: Python ------------------------------------------

PYTHON_SOURCE = '''\
import os

TIMEOUT = 30


def standalone(x):
    return x * 2


class Greeter:
    def __init__(self, name):
        self.name = name

    def greet(self):
        return f"Hola, {self.name}"
'''


def test_python_module_level_code_becomes_raw_chunk():
    chunks = chunk_with_treesitter("mod.py", PYTHON_SOURCE, "python")
    raw = [c for c in chunks if c.chunk_type == "raw"]
    assert len(raw) == 1
    assert "import os" in raw[0].code
    assert "TIMEOUT = 30" in raw[0].code
    assert raw[0].name == "module_level"


def test_python_function_becomes_own_chunk():
    chunks = chunk_with_treesitter("mod.py", PYTHON_SOURCE, "python")
    funcs = [c for c in chunks if c.chunk_type == "function" and c.name == "standalone"]
    assert len(funcs) == 1
    assert "return x * 2" in funcs[0].code
    assert funcs[0].language == "python"


def test_python_class_and_its_methods_are_separate_chunks():
    chunks = chunk_with_treesitter("mod.py", PYTHON_SOURCE, "python")
    classes = [c for c in chunks if c.chunk_type == "class" and c.name == "Greeter"]
    # la gramática de Python no distingue "method_definition" de
    # "function_definition" (a diferencia de JS/Java) — un método dentro de
    # una clase se clasifica como "function", igual que uno top-level, solo
    # que aparece como chunk propio en vez de quedar embebido en la clase.
    method_names = {c.name for c in chunks if c.chunk_type == "function" and c.name in ("__init__", "greet")}

    assert len(classes) == 1
    assert method_names == {"__init__", "greet"}
    # el chunk de la clase debe incluir el cuerpo completo, no solo la firma
    assert "def greet" in classes[0].code


def test_python_line_ranges_are_1_indexed_and_consistent():
    chunks = chunk_with_treesitter("mod.py", PYTHON_SOURCE, "python")
    func = next(c for c in chunks if c.name == "standalone")
    # "def standalone(x):" está en la línea 6 del fixture (1-indexado, dos
    # líneas en blanco de por medio tras TIMEOUT = 30)
    assert func.start_line == 6
    assert func.end_line >= func.start_line
    assert func.code.count("\n") + 1 >= (func.end_line - func.start_line + 1) - 1


PYTHON_DECORATED = '''\
def plain():
    pass


@decorador_auditoria
def con_decorador(x):
    return x
'''


def test_python_decorator_is_included_in_chunk_code_and_start_line():
    chunks = chunk_with_treesitter("mod.py", PYTHON_DECORATED, "python")
    decorated = next(c for c in chunks if c.name == "con_decorador")
    assert decorated.code.startswith("@decorador_auditoria")
    assert decorated.start_line == 5  # línea del decorador, no de la def


PYTHON_WITH_DOC_COMMENT = '''\
# Comentario que documenta la función siguiente
def documentada():
    return 1


# Comentario suelto, no pega con lo de abajo

def no_documentada():
    return 2
'''


def test_leading_comment_without_blank_line_is_attached_to_chunk():
    chunks = chunk_with_treesitter("mod.py", PYTHON_WITH_DOC_COMMENT, "python")
    documentada = next(c for c in chunks if c.name == "documentada")
    assert "# Comentario que documenta la función siguiente" in documentada.code


def test_leading_comment_with_blank_line_is_not_attached():
    chunks = chunk_with_treesitter("mod.py", PYTHON_WITH_DOC_COMMENT, "python")
    no_documentada = next(c for c in chunks if c.name == "no_documentada")
    assert "Comentario suelto" not in no_documentada.code
    # en cambio debe quedar en el chunk de nivel de módulo
    raw = next(c for c in chunks if c.chunk_type == "raw")
    assert "Comentario suelto" in raw.code


def test_no_duplicate_chunk_ids_for_python_file():
    chunks = chunk_with_treesitter("mod.py", PYTHON_SOURCE, "python")
    ids = [c.chunk_id() for c in chunks]
    assert len(ids) == len(set(ids))


# --- chunk_with_treesitter: otros lenguajes (prueba que es "políglota") ----

JS_SOURCE = '''\
function suma(a, b) {
    return a + b;
}

class Contador {
    incrementar() {
        this.n += 1;
    }
}
'''


def test_javascript_function_and_class_are_chunked():
    chunks = chunk_with_treesitter("app.js", JS_SOURCE, "javascript")
    names = {c.name for c in chunks}
    assert "suma" in names
    assert "Contador" in names
    assert "incrementar" in names


GO_SOURCE = '''\
package main

type Config struct {
	Timeout int
}

func Load() *Config {
	return &Config{Timeout: 30}
}
'''


def test_go_type_declaration_is_classified_as_class():
    chunks = chunk_with_treesitter("main.go", GO_SOURCE, "go")
    config_chunk = next(c for c in chunks if c.name == "Config")
    assert config_chunk.chunk_type == "class"
    load_chunk = next(c for c in chunks if c.name == "Load")
    assert load_chunk.chunk_type == "function"


# --- chunk_fallback ----------------------------------------------------------

def test_chunk_fallback_splits_large_file_into_multiple_chunks():
    long_source = "\n".join(f"linea {i} " + "x" * 40 for i in range(200))
    chunks = chunk_fallback("notas.txt", long_source)
    assert len(chunks) > 1
    assert all(c.language == "unknown" and c.chunk_type == "raw" for c in chunks)


def test_chunk_fallback_single_chunk_for_small_file():
    chunks = chunk_fallback("notas.txt", "solo una línea corta")
    assert len(chunks) == 1
    assert chunks[0].code == "solo una línea corta"


def test_chunk_fallback_consecutive_chunks_overlap():
    long_source = "\n".join(f"linea {i} " + "x" * 40 for i in range(200))
    chunks = chunk_fallback("notas.txt", long_source)
    # el fallback retrocede unas líneas entre chunk y chunk para no cortar contexto
    assert chunks[1].start_line <= chunks[0].end_line


# --- chunk_file (punto de entrada, vía filesystem real) ----------------------

def test_chunk_file_dispatches_treesitter_for_known_extension(tmp_path):
    f = tmp_path / "mod.py"
    f.write_text(PYTHON_SOURCE, encoding="utf-8")
    chunks = chunk_file(str(f))
    assert any(c.name == "standalone" for c in chunks)
    assert all(c.language == "python" for c in chunks if c.chunk_type != "raw" or c.name == "module_level")


def test_chunk_file_uses_fallback_for_unknown_extension(tmp_path):
    f = tmp_path / "notas.txt"
    f.write_text("contenido cualquiera sin lenguaje reconocido", encoding="utf-8")
    chunks = chunk_file(str(f))
    assert chunks[0].language == "unknown"


def test_chunk_file_falls_back_when_treesitter_parser_raises(tmp_path, monkeypatch):
    import core.engine.chunker as chunker_module

    def _boom(*args, **kwargs):
        raise RuntimeError("parser roto")

    monkeypatch.setattr(chunker_module, "chunk_with_treesitter", _boom)

    f = tmp_path / "mod.py"
    f.write_text(PYTHON_SOURCE, encoding="utf-8")
    chunks = chunker_module.chunk_file(str(f))

    # no se pierde el archivo: cae a chunking por tamaño en vez de propagar la excepción
    assert len(chunks) >= 1
    assert chunks[0].language == "unknown"


# --- CodeChunk -----------------------------------------------------------

def test_code_chunk_to_metadata_and_chunk_id():
    chunks = chunk_with_treesitter("mod.py", PYTHON_SOURCE, "python")
    func = next(c for c in chunks if c.name == "standalone")

    metadata = func.to_metadata()
    assert metadata["file_path"] == "mod.py"
    assert metadata["chunk_type"] == "function"
    assert metadata["name"] == "standalone"

    assert func.chunk_id() == f"mod.py::{func.start_line}-{func.end_line}"
