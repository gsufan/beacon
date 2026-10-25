"""Model answer cleanup: drops the courtesy phrase that sometimes precedes the content."""

_PREAMBLE_MARKERS = (
    "respuesta completa", "en español", "en inglés", "in english", "the response",
    "la respuesta", "i will respond", "aquí está", "aquí tienes", "a continuación",
    "below is", "here is", "responderé",
)


def strip_preamble(text: str, heading_marker: str = "##") -> str:
    """Drops meta-commentary preambles before the real content. Two steps: (1) if a markdown heading appears
    near the start, everything before it is preamble; (2) if the first line is short and mentions the
    language or the answer instead of content, it is dropped.
    """
    heading_idx = text.find(heading_marker)
    if 0 < heading_idx < 300:
        text = text[heading_idx:].strip()

    first_line, _, rest = text.partition("\n")
    if rest and len(first_line) < 200 and any(m in first_line.lower() for m in _PREAMBLE_MARKERS):
        return rest.lstrip("\n").strip()
    return text
