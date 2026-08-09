"""
Chunker Políglota — Pilar 4A de la propuesta.

En vez de dividir el código por líneas o caracteres (lo que rompe funciones
y clases a la mitad), este módulo usa tree-sitter para parsear el AST real
de cada lenguaje y extraer bloques semánticamente completos.

Cada chunk resultante contiene: código completo del bloque, tipo (función,
clase, método), nombre, lenguaje, archivo de origen y rango de líneas.
Estos metadatos son los que luego se guardan junto al embedding en ChromaDB.
"""

import warnings
from dataclasses import dataclass, field
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
        return f"{self.file_path}::{self.start_line}-{self.end_line}"


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


def chunk_with_treesitter(file_path: str, source_code: str, language: str) -> List[CodeChunk]:
    parser = get_parser(language)
    source_bytes = source_code.encode("utf-8")
    tree = parser.parse(source_bytes)

    chunkable_types = CHUNKABLE_NODE_TYPES.get(language, set())
    wrapper_type = DECORATOR_WRAPPER_TYPE.get(language)
    chunks: List[CodeChunk] = []
    covered_ranges: List[tuple] = []
    consumed_comment_starts = set()  # start_byte de comentarios ya adjuntados a un chunk

    def walk(node, depth=0):
        # Caso especial: función/clase decorada. El nodo contenedor abarca
        # los decoradores + la definición interna; usamos su rango completo
        # para no perder los decoradores, pero el nombre/tipo salen de la
        # definición interna real.
        if wrapper_type and node.type == wrapper_type:
            inner = next((c for c in node.children if c.type in chunkable_types), None)
            if inner is not None:
                leading_comments = _collect_leading_comment_nodes(node)
                comment_prefix = ""
                start_byte = node.start_byte
                start_line = node.start_point[0] + 1
                if leading_comments:
                    comment_prefix = source_bytes[leading_comments[0].start_byte:node.start_byte].decode("utf-8", errors="ignore")
                    start_byte = leading_comments[0].start_byte
                    start_line = leading_comments[0].start_point[0] + 1
                    consumed_comment_starts.update(c.start_byte for c in leading_comments)

                code = comment_prefix + source_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="ignore")
                name = _extract_name(inner, source_bytes)
                chunk_type = _node_type_to_chunk_type(inner.type)
                chunks.append(CodeChunk(
                    file_path=file_path,
                    language=language,
                    chunk_type=chunk_type,
                    name=name,
                    code=code,
                    start_line=start_line,
                    end_line=node.end_point[0] + 1,
                ))
                covered_ranges.append((start_byte, node.end_byte))
                if chunk_type == "class":
                    # Seguimos bajando dentro de la clase para capturar sus
                    # métodos (decorados o no) como chunks propios también.
                    for child in inner.children:
                        walk(child, depth + 1)
                return
        if node.type in chunkable_types:
            leading_comments = _collect_leading_comment_nodes(node)
            comment_prefix = ""
            start_byte = node.start_byte
            start_line = node.start_point[0] + 1
            if leading_comments:
                comment_prefix = source_bytes[leading_comments[0].start_byte:node.start_byte].decode("utf-8", errors="ignore")
                start_byte = leading_comments[0].start_byte
                start_line = leading_comments[0].start_point[0] + 1
                consumed_comment_starts.update(c.start_byte for c in leading_comments)

            code = comment_prefix + source_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="ignore")
            name = _extract_name(node, source_bytes)
            chunks.append(CodeChunk(
                file_path=file_path,
                language=language,
                chunk_type=_node_type_to_chunk_type(node.type),
                name=name,
                code=code,
                start_line=start_line,
                end_line=node.end_point[0] + 1,
            ))
            covered_ranges.append((start_byte, node.end_byte))
            # No seguimos bajando dentro de una función ya capturada como chunk,
            # salvo para clases: sus métodos internos también queremos como chunks propios.
            if _node_type_to_chunk_type(node.type) != "class":
                return
        for child in node.children:
            walk(child, depth + 1)

    walk(tree.root_node)

    # Código a nivel de módulo que no cayó dentro de ninguna función/clase
    # (imports, constantes globales, configuración) se agrupa en un chunk "raw"
    # aparte, para no perder ese contexto en la recuperación semántica.
    # Los comentarios ya adjuntados como docstring de algún chunk (arriba) se
    # excluyen aquí para no duplicarlos.
    top_level_pieces = []
    for child in tree.root_node.children:
        if child.start_byte in consumed_comment_starts:
            continue
        if child.type not in chunkable_types and child.type != wrapper_type:
            snippet = source_bytes[child.start_byte:child.end_byte].decode("utf-8", errors="ignore")
            if snippet.strip():
                top_level_pieces.append(snippet)
    if top_level_pieces:
        module_code = "\n".join(top_level_pieces)
        chunks.insert(0, CodeChunk(
            file_path=file_path,
            language=language,
            chunk_type="raw",
            name="module_level",
            code=module_code,
            start_line=1,
            end_line=module_code.count("\n") + 1,
        ))

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
    """Punto de entrada principal: detecta lenguaje y aplica la estrategia correcta."""
    path = Path(file_path)
    source_code = path.read_text(encoding="utf-8", errors="ignore")
    language = detect_language(file_path)

    if language is None:
        return chunk_fallback(file_path, source_code)

    try:
        return chunk_with_treesitter(file_path, source_code, language)
    except Exception:
        # Si el parser falla (sintaxis rota, versión de gramática, etc.) no perdemos el archivo
        return chunk_fallback(file_path, source_code)


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
