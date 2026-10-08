"""
Chunker Políglota — Pilar 4A de la propuesta.

En vez de dividir el código por líneas o caracteres (lo que rompe funciones
y clases a la mitad), este módulo usa tree-sitter para parsear el AST real
de cada lenguaje y extraer bloques semánticamente completos.

Cada chunk resultante contiene: código completo del bloque, tipo (función,
clase, método), nombre, lenguaje, archivo de origen y rango de líneas.
Estos metadatos son los que luego se guardan junto al embedding en ChromaDB.
"""

import re
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

# Ruido inofensivo: tree-sitter-languages usa una API antigua de tree-sitter
# (funciona bien, solo avisa). No es información accionable para el usuario.
warnings.filterwarnings("ignore", message=r"Language\(path, name\) is deprecated.*")

from tree_sitter_languages import get_parser


# Extensión de archivo -> nombre de lenguaje reconocido por tree-sitter-languages
EXTENSION_TO_LANGUAGE = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".java": "java",
    ".go": "go",
    ".cs": "c_sharp",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".hpp": "cpp",
    ".c": "c",
    ".h": "c",
    ".rs": "rust",
    ".php": "php",
}

# Tipos de nodos AST que consideramos "unidades de chunk" por lenguaje.
# Estos son los nombres de nodo que expone la gramática de cada lenguaje.
CHUNKABLE_NODE_TYPES = {
    "python": {"function_definition", "class_definition"},
    "javascript": {"function_declaration", "class_declaration", "method_definition"},
    "typescript": {"function_declaration", "class_declaration", "method_definition", "interface_declaration"},
    "tsx": {"function_declaration", "class_declaration", "method_definition", "interface_declaration"},
    "java": {"method_declaration", "class_declaration", "interface_declaration", "constructor_declaration"},
    "go": {"function_declaration", "method_declaration", "type_declaration"},
    "c_sharp": {"method_declaration", "class_declaration", "constructor_declaration"},
    "cpp": {"function_definition", "class_specifier", "struct_specifier"},
    "c": {"function_definition", "struct_specifier"},
    "rust": {"function_item", "impl_item", "struct_item", "enum_item"},
    "php": {"function_definition", "class_declaration", "method_declaration"},
}

# Algunos lenguajes envuelven una función/clase decorada en un nodo contenedor
# aparte (en vez de que el decorador cuelgue como hermano suelto). Si no se
# maneja, el chunker corta el decorador fuera del rango de la función.
DECORATOR_WRAPPER_TYPE = {
    "python": "decorated_definition",
}

# `const Boton = () => {...}` (el estilo de casi todo componente React) no es
# un function_declaration: se reconoce por el valor asignado al declarador.
ASSIGNED_FUNCTION_LANGUAGES = {"javascript", "typescript", "tsx"}
ASSIGNED_FUNCTION_VALUE_TYPES = {"arrow_function", "function", "function_expression"}

# Lo que queda de un `export ...` o `namespace X { ... }` al restarle los
# chunks que contiene: envoltorio sin contenido propio, no vale la pena indexarlo.
_TRIVIAL_WRAPPER_RE = re.compile(r"^(export( default)?|(declare )?(namespace|module) [\w.\"']+)?$")

# Tamaño máximo de un chunk "fallback" (para archivos sin lenguaje soportado
# o fragmentos de código fuera de cualquier función/clase, como imports globales)
FALLBACK_CHUNK_SIZE = 1500
FALLBACK_OVERLAP = 200


@dataclass
class CodeChunk:
    file_path: str
    language: str
    chunk_type: str          # "function", "class", "method", "raw"
    name: Optional[str]
    code: str
    start_line: int
    end_line: int

    def to_metadata(self) -> dict:
        """Metadata a guardar junto al embedding en ChromaDB."""
        return {
            "file_path": self.file_path,
            "language": self.language,
            "chunk_type": self.chunk_type,
            "name": self.name or "",
            "start_line": self.start_line,
            "end_line": self.end_line,
        }

    def chunk_id(self) -> str:
        """ID determinístico: mismo archivo+rango => mismo id (clave para re-indexación incremental)."""
        if self.name == MODULE_LEVEL_NAME:
            # su rango puede compartir líneas con un chunk real (`const a = 1; function f() {}`)
            return f"{self.file_path}::{MODULE_LEVEL_NAME}"
        return f"{self.file_path}::{self.start_line}-{self.end_line}"


MODULE_LEVEL_NAME = "module_level"


def detect_language(file_path: str) -> Optional[str]:
    ext = Path(file_path).suffix.lower()
    return EXTENSION_TO_LANGUAGE.get(ext)


def _extract_name(node, source_bytes: bytes) -> Optional[str]:
    """Busca un nodo de tipo 'identifier' o similar para nombrar el chunk.
    Primero en los hijos directos; si no aparece, un nivel más profundo
    (necesario en Go: type_declaration -> type_spec -> type_identifier)."""
    identifier_types = ("identifier", "type_identifier", "property_identifier", "field_identifier")

    for child in node.children:
        if child.type in identifier_types:
            return source_bytes[child.start_byte:child.end_byte].decode("utf-8", errors="ignore")

    for child in node.children:
        for grandchild in child.children:
            if grandchild.type in identifier_types:
                return source_bytes[grandchild.start_byte:grandchild.end_byte].decode("utf-8", errors="ignore")

    return None


def _node_type_to_chunk_type(node_type: str) -> str:
    # Casos explícitos por nombre exacto de nodo, para lenguajes cuyo nodo
    # contenedor no incluye la palabra clave en su propio nombre (ej: Go
    # usa "type_declaration" tanto para structs como interfaces/alias, sin
    # que el string contenga "struct").
    if node_type == "type_declaration":
        return "class"
    if "class" in node_type or "struct" in node_type or "interface" in node_type or "impl" in node_type or "enum" in node_type:
        return "class"
    if "method" in node_type or "constructor" in node_type:
        return "method"
    return "function"


# Tipos de nodo de comentario reconocidos por las distintas gramáticas
# (todas las soportadas usan el mismo nombre "comment").
COMMENT_NODE_TYPE = "comment"


def _collect_leading_comment_nodes(node) -> list:
    """
    Recolecta los nodos de comentario que preceden INMEDIATAMENTE a 'node'
    (sin línea en blanco de por medio), el patrón estándar de un docstring/
    doc-comment en Go, Java, JS/TS, C/C++ (ej: '// CreateQuoteFromCount
    takes...' justo antes de 'func CreateQuoteFromCount(...)').

    Si hay una línea en blanco entre el comentario y el nodo, NO se
    considera documentación de ese nodo (podría ser un comentario suelto
    sobre otra cosa), y se deja tal cual en el chunk 'module_level'.

    Devuelve los nodos en orden de aparición (de arriba hacia abajo).
    """
    comments = []
    current = node.prev_sibling
    expected_end_line = node.start_point[0] - 1  # línea inmediatamente anterior

    while current is not None and current.type == COMMENT_NODE_TYPE:
        if current.end_point[0] != expected_end_line:
            break
        comments.append(current)
        expected_end_line = current.start_point[0] - 1
        current = current.prev_sibling

    comments.reverse()
    return comments


def _byte_to_line(source_bytes: bytes, offset: int) -> int:
    return source_bytes.count(b"\n", 0, offset) + 1


def _is_overload_stub(decorated, source_bytes: bytes) -> bool:
    """Firma `@overload` de Python (`def f(x: int) -> str: ...`): solo declara
    tipos y no tiene cuerpo; la implementación real viene después."""
    for child in decorated.children:
        if child.type == "decorator":
            text = source_bytes[child.start_byte:child.end_byte].decode("utf-8", errors="ignore")
            target = text.lstrip("@").strip()
            if target == "overload" or target.endswith(".overload"):
                return True
    return False


def _with_export(node):
    """`export function f` / `export class C`: el chunk incluye el `export` y el
    JSDoc que va antes, que en el AST cuelgan del export_statement, no de la función."""
    parent = node.parent
    return parent if parent is not None and parent.type == "export_statement" else node


def _assigned_function_name(declarator, source_bytes: bytes) -> Optional[str]:
    name_node = declarator.child_by_field_name("name")
    if name_node is None or name_node.type != "identifier":
        return None  # destructuring (`const {a, b} = ...`), no es una función con nombre
    return source_bytes[name_node.start_byte:name_node.end_byte].decode("utf-8", errors="ignore")


def chunk_with_treesitter(file_path: str, source_code: str, language: str) -> List[CodeChunk]:
    parser = get_parser(language)
    source_bytes = source_code.encode("utf-8")
    tree = parser.parse(source_bytes)

    chunkable_types = CHUNKABLE_NODE_TYPES.get(language, set())
    wrapper_type = DECORATOR_WRAPPER_TYPE.get(language)
    chunks: List[CodeChunk] = []
    covered_ranges: List[tuple] = []  # (start_byte, end_byte) ya incluidos en algún chunk

    def emit(range_node, name: Optional[str], chunk_type: str):
        # range_node abarca todo lo que va al chunk (decoradores, `export`); los
        # comentarios pegados justo encima se suman como su documentación.
        leading = _collect_leading_comment_nodes(range_node)
        first = leading[0] if leading else range_node
        chunks.append(CodeChunk(
            file_path=file_path,
            language=language,
            chunk_type=chunk_type,
            name=name,
            code=source_bytes[first.start_byte:range_node.end_byte].decode("utf-8", errors="ignore"),
            start_line=first.start_point[0] + 1,
            end_line=range_node.end_point[0] + 1,
        ))
        covered_ranges.append((first.start_byte, range_node.end_byte))

    def walk(node):
        # Función/clase decorada: el contenedor abarca decoradores + definición;
        # nombre y tipo salen de la definición interna.
        if wrapper_type and node.type == wrapper_type:
            inner = next((c for c in node.children if c.type in chunkable_types), None)
            if inner is not None and _is_overload_stub(node, source_bytes):
                # No se indexa: con el mismo nombre que la implementación y sin
                # cuerpo, ocupaba lugares del contexto sin aportar (medido en
                # psf/requests: tres firmas de __init__ desplazaban a
                # get_netrc_auth fuera del contexto). Se marca como cubierta
                # para que tampoco vaya al fragmento de nivel de módulo.
                covered_ranges.append((node.start_byte, node.end_byte))
                return
            if inner is not None:
                chunk_type = _node_type_to_chunk_type(inner.type)
                emit(node, _extract_name(inner, source_bytes), chunk_type)
                if chunk_type == "class":
                    for child in inner.children:
                        walk(child)
                return

        if node.type in chunkable_types:
            chunk_type = _node_type_to_chunk_type(node.type)
            emit(_with_export(node), _extract_name(node, source_bytes), chunk_type)
            # Dentro de una función no se sigue bajando; dentro de una clase sí,
            # para capturar sus métodos como chunks propios.
            if chunk_type != "class":
                return

        elif language in ASSIGNED_FUNCTION_LANGUAGES and node.type == "variable_declarator":
            value = node.child_by_field_name("value")
            if value is not None and value.type in ASSIGNED_FUNCTION_VALUE_TYPES:
                range_node = node
                declaration = node.parent
                if (declaration is not None
                        and declaration.type in ("lexical_declaration", "variable_declaration")
                        and sum(c.type == "variable_declarator" for c in declaration.children) == 1):
                    range_node = _with_export(declaration)
                emit(range_node, _assigned_function_name(node, source_bytes), "function")
                return

        for child in node.children:
            walk(child)

    walk(tree.root_node)

    module_chunk = _module_level_chunk(file_path, language, source_bytes, tree.root_node, covered_ranges)
    if module_chunk is not None:
        chunks.insert(0, module_chunk)

    # Si no se encontró NADA chunkeable ni a nivel de módulo, guardamos el archivo entero
    if not chunks:
        chunks.append(CodeChunk(
            file_path=file_path,
            language=language,
            chunk_type="raw",
            name=None,
            code=source_code,
            start_line=1,
            end_line=source_code.count("\n") + 1,
        ))

    return chunks


def _module_level_chunk(file_path, language, source_bytes: bytes, root, covered_ranges) -> Optional[CodeChunk]:
    """Código fuera de toda función/clase (imports, constantes, configuración),
    agrupado en un chunk "raw" para no perder ese contexto en la búsqueda.

    Se calcula RESTANDO los rangos ya cubiertos por otros chunks, no por tipo de
    nodo: así lo que vive dentro de un `export ...` o de un `namespace X { ... }`
    no se duplica acá."""
    covered = sorted(covered_ranges)

    def uncovered(start: int, end: int) -> List[tuple]:
        segments, cursor = [], start
        for cs, ce in covered:
            if ce <= cursor or cs >= end:
                continue
            if cs > cursor:
                segments.append((cursor, cs))
            cursor = max(cursor, ce)
        if cursor < end:
            segments.append((cursor, end))
        return segments

    pieces: List[str] = []
    spans: List[tuple] = []
    for child in root.children:
        segments = uncovered(child.start_byte, child.end_byte)
        texts, child_spans = [], []
        for s, e in segments:
            raw = source_bytes[s:e]
            stripped = raw.strip()
            if not stripped:
                continue
            lead = len(raw) - len(raw.lstrip())
            texts.append(stripped.decode("utf-8", errors="ignore"))
            child_spans.append((s + lead, s + lead + len(stripped) - 1))
        if not texts:
            continue
        wraps_chunks = segments != [(child.start_byte, child.end_byte)]
        if wraps_chunks and _TRIVIAL_WRAPPER_RE.match(re.sub(r"[\s{}();,]+", " ", " ".join(texts)).strip()):
            continue
        pieces.append("\n".join(texts))
        spans.extend(child_spans)

    if not pieces:
        return None
    return CodeChunk(
        file_path=file_path,
        language=language,
        chunk_type="raw",
        name=MODULE_LEVEL_NAME,
        code="\n".join(pieces),
        # rango real que abarca las piezas (no son contiguas, pero la cita apunta al lugar correcto)
        start_line=_byte_to_line(source_bytes, spans[0][0]),
        end_line=_byte_to_line(source_bytes, spans[-1][1]),
    )


def chunk_fallback(file_path: str, source_code: str) -> List[CodeChunk]:
    """Para lenguajes sin gramática tree-sitter soportada: chunking por tamaño con overlap."""
    lines = source_code.split("\n")
    chunks: List[CodeChunk] = []
    start = 0
    while start < len(lines):
        end = start
        char_count = 0
        while end < len(lines) and char_count < FALLBACK_CHUNK_SIZE:
            char_count += len(lines[end]) + 1
            end += 1
        code = "\n".join(lines[start:end])
        chunks.append(CodeChunk(
            file_path=file_path,
            language="unknown",
            chunk_type="raw",
            name=None,
            code=code,
            start_line=start + 1,
            end_line=end,
        ))
        if end >= len(lines):
            break
        # overlap: retrocedemos algunas líneas para no cortar contexto
        overlap_lines = max(1, FALLBACK_OVERLAP // 80)
        start = end - overlap_lines
    return chunks


def chunk_file(file_path: str) -> List[CodeChunk]:
    """Punto de entrada desde el disco: lee el archivo y lo fragmenta."""
    source_code = Path(file_path).read_text(encoding="utf-8", errors="ignore")
    return chunk_source(file_path, source_code)


def chunk_source(file_path: str, source_code: str) -> List[CodeChunk]:
    """Fragmenta un contenido ya leído (ej. desde un commit de git). `file_path`
    solo se usa para detectar el lenguaje y como identidad de los chunks."""
    if not source_code.strip():
        return []  # archivo vacío (ej. un __init__.py): nada que buscar ni documentar
    language = detect_language(file_path)

    if language is None:
        return chunk_fallback(file_path, source_code)

    try:
        return _unique_ids(chunk_with_treesitter(file_path, source_code, language))
    except Exception:
        # Si el parser falla (sintaxis rota, versión de gramática, etc.) no perdemos el archivo
        return chunk_fallback(file_path, source_code)


def _unique_ids(chunks: List[CodeChunk]) -> List[CodeChunk]:
    """Descarta los chunks cuyo id ya salió antes. Dos funciones en las mismas líneas
    (`{ get x() {}, set x(v) {} }` en una sola línea) comparten rango y, por lo tanto,
    id; Chroma rechaza el upsert completo si hay ids repetidos y el sync del repo falla."""
    seen = set()
    unique = []
    for chunk in chunks:
        cid = chunk.chunk_id()
        if cid not in seen:
            seen.add(cid)
            unique.append(chunk)
    return unique


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2:
        print("Uso: python chunker.py <archivo>")
        sys.exit(1)

    result = chunk_file(sys.argv[1])
    print(f"\n{len(result)} chunk(s) extraídos de {sys.argv[1]}\n" + "=" * 60)
    for c in result:
        print(f"[{c.chunk_type.upper()}] {c.name or '(sin nombre)'}  "
              f"líneas {c.start_line}-{c.end_line}  ({c.language})")
        print("-" * 60)
        print(c.code[:200] + ("..." if len(c.code) > 200 else ""))
        print()
