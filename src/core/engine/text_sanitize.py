"""Limpieza de respuestas de LLM: modelos chicos a veces anteponen una frase
reconociendo las instrucciones (ej. "La respuesta completa en español es:")
antes del contenido real. Esta heurística la descarta.
"""

_PREAMBLE_MARKERS = (
    "respuesta completa", "en español", "en inglés", "in english", "the response",
    "la respuesta", "i will respond", "aquí está", "aquí tienes", "a continuación",
    "below is", "here is", "responderé",
)


def strip_preamble(text: str, heading_marker: str = "##") -> str:
    """Descarta preámbulos de meta-comentario antes del contenido real.

    Heurística en dos pasos: (1) si hay un encabezado markdown cerca del
    inicio, todo lo anterior es preámbulo; (2) si la primera línea es corta y
    menciona el idioma/la respuesta en vez de contenido real, se descarta.
    """
    heading_idx = text.find(heading_marker)
    if 0 < heading_idx < 300:
        text = text[heading_idx:].strip()

    first_line, _, rest = text.partition("\n")
    if rest and len(first_line) < 200 and any(m in first_line.lower() for m in _PREAMBLE_MARKERS):
        return rest.lstrip("\n").strip()
    return text
