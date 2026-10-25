"""Polyglot AST-based chunking (tree-sitter): splits code into whole functions, classes and methods."""

import re
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

# tree-sitter-languages uses an old tree-sitter API and emits a harmless warning.
warnings.filterwarnings("ignore", message=r"Language\(path, name\) is deprecated.*")

from tree_sitter_languages import get_parser


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

# Some languages wrap a decorated function or class in a container node; it is included whole
# so the decorator stays inside the chunk.
DECORATOR_WRAPPER_TYPE = {
    "python": "decorated_definition",
}

# `const Button = () => {...}` is not a function_declaration: it is recognized by the value assigned to the declarator.
ASSIGNED_FUNCTION_LANGUAGES = {"javascript", "typescript", "tsx"}
ASSIGNED_FUNCTION_VALUE_TYPES = {"arrow_function", "function", "function_expression"}

# What remains of an `export ...` or `namespace X { ... }` after subtracting the chunks it contains is an
# empty wrapper and is not worth indexing.
_TRIVIAL_WRAPPER_RE = re.compile(r"^(export( default)?|(declare )?(namespace|module) [\w.\"']+)?$")

FALLBACK_CHUNK_SIZE = 1500
FALLBACK_OVERLAP = 200


@dataclass
class CodeChunk:
    file_path: str
    language: str
    chunk_type: str
    name: Optional[str]
    code: str
    start_line: int
    end_line: int

    def to_metadata(self) -> dict:
        return {
            "file_path": self.file_path,
            "language": self.language,
            "chunk_type": self.chunk_type,
            "name": self.name or "",
            "start_line": self.start_line,
            "end_line": self.end_line,
        }

    def chunk_id(self) -> str:
        """Deterministic id: same file and range, same id (needed for incremental reindexing)."""
        if self.name == MODULE_LEVEL_NAME:
            # its range may share lines with a real chunk (`const a = 1; function f() {}`)
            return f"{self.file_path}::{MODULE_LEVEL_NAME}"
        return f"{self.file_path}::{self.start_line}-{self.end_line}"


MODULE_LEVEL_NAME = "module_level"


def detect_language(file_path: str) -> Optional[str]:
    ext = Path(file_path).suffix.lower()
    return EXTENSION_TO_LANGUAGE.get(ext)


def _extract_name(node, source_bytes: bytes) -> Optional[str]:
    """Finds the identifier node that names the chunk: in the direct children first, then one level
    deeper (needed in Go: type_declaration -> type_spec -> type_identifier).
    """
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
    # Explicit cases by exact node name, for languages whose container node does not include the keyword
    # in its own name (Go uses "type_declaration" for structs, interfaces and aliases alike).
    if node_type == "type_declaration":
        return "class"
    if "class" in node_type or "struct" in node_type or "interface" in node_type or "impl" in node_type or "enum" in node_type:
        return "class"
    if "method" in node_type or "constructor" in node_type:
        return "method"
    return "function"


COMMENT_NODE_TYPE = "comment"


def _collect_leading_comment_nodes(node) -> list:
    """Comment nodes immediately preceding `node` (no blank line in between), the usual doc-comment
    pattern in Go, Java, JS/TS and C/C++. A comment separated by a blank line is not documentation of
    the node and stays in the module-level chunk. Returned in source order.
    """
    comments = []
    current = node.prev_sibling
    expected_end_line = node.start_point[0] - 1

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
    """Python `@overload` signature: it only declares types and has no body."""
    for child in decorated.children:
        if child.type == "decorator":
            text = source_bytes[child.start_byte:child.end_byte].decode("utf-8", errors="ignore")
            target = text.lstrip("@").strip()
            if target == "overload" or target.endswith(".overload"):
                return True
    return False


def _with_export(node):
    """For `export function f` / `export class C`: the chunk includes the `export` and the JSDoc, which hang from the export_statement in the AST."""
    parent = node.parent
    return parent if parent is not None and parent.type == "export_statement" else node


def _assigned_function_name(declarator, source_bytes: bytes) -> Optional[str]:
    name_node = declarator.child_by_field_name("name")
    if name_node is None or name_node.type != "identifier":
        return None  # destructuring (`const {a, b} = ...`): not a named function
    return source_bytes[name_node.start_byte:name_node.end_byte].decode("utf-8", errors="ignore")


def chunk_with_treesitter(file_path: str, source_code: str, language: str) -> List[CodeChunk]:
    parser = get_parser(language)
    source_bytes = source_code.encode("utf-8")
    tree = parser.parse(source_bytes)

    chunkable_types = CHUNKABLE_NODE_TYPES.get(language, set())
    wrapper_type = DECORATOR_WRAPPER_TYPE.get(language)
    chunks: List[CodeChunk] = []
    covered_ranges: List[tuple] = []  # (start_byte, end_byte) already included in some chunk

    def emit(range_node, name: Optional[str], chunk_type: str):
        # range_node covers everything that goes into the chunk (decorators, `export`); comments right above
        # are added as its documentation.
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
        if wrapper_type and node.type == wrapper_type:
            inner = next((c for c in node.children if c.type in chunkable_types), None)
            if inner is not None and _is_overload_stub(node, source_bytes):
                # Not indexed: it has the implementation's name and no body, so it would only take up context.
                # It is marked as covered so it does not end up in the module-level chunk either.
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
            # Do not descend into functions; do descend into classes to capture their methods as separate chunks.
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
    """Code outside any function or class (imports, constants, configuration) as a single "raw" chunk.
    Computed by subtracting the ranges already covered by other chunks, not by node type, so what lives
    inside an `export ...` or a `namespace X { ... }` is not duplicated.
    """
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
        # real range spanned by the pieces (not contiguous, but the citation points to the right place)
        start_line=_byte_to_line(source_bytes, spans[0][0]),
        end_line=_byte_to_line(source_bytes, spans[-1][1]),
    )


def chunk_fallback(file_path: str, source_code: str) -> List[CodeChunk]:
    """Size-based chunking with overlap, for languages without a supported grammar."""
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
        overlap_lines = max(1, FALLBACK_OVERLAP // 80)
        start = end - overlap_lines
    return chunks


def chunk_file(file_path: str) -> List[CodeChunk]:
    source_code = Path(file_path).read_text(encoding="utf-8", errors="ignore")
    return chunk_source(file_path, source_code)


def chunk_source(file_path: str, source_code: str) -> List[CodeChunk]:
    """`file_path` is only used to detect the language and as the identity of the chunks."""
    if not source_code.strip():
        return []  # empty file (e.g. an __init__.py): nothing to search or document
    language = detect_language(file_path)

    if language is None:
        return chunk_fallback(file_path, source_code)

    try:
        return _unique_ids(chunk_with_treesitter(file_path, source_code, language))
    except Exception:
        # If the parser fails (broken syntax, grammar version) the file is not lost
        return chunk_fallback(file_path, source_code)


def _unique_ids(chunks: List[CodeChunk]) -> List[CodeChunk]:
    """Drops chunks whose id was already seen. Two definitions on the same line share a range and
    therefore an id, and Chroma rejects the whole upsert when ids repeat.
    """
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
