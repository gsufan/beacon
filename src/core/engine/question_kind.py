"""Tells open questions about the whole repository apart from questions about specific code."""

import re

# "What does this project do?", "how is it organized?", "which are the main components?" (Spanish and English).
_SUBJECT = r"(proyecto|repositorio|repo|sistema|aplicaci[oó]n|app|programa|c[oó]digo|software|librer[ií]a|biblioteca|project|repository|codebase|system|application|library)"
_GENERAL = [
    rf"\bqu[eé] hace (este|esta|el|la|todo el|todo este)\s+{_SUBJECT}\b",
    rf"\bpara qu[eé] sirve (este|esta|el|la)\s+{_SUBJECT}\b",
    rf"\bde qu[eé] (trata|se trata|va) (este|esta|el|la)\s+{_SUBJECT}\b",
    rf"\bqu[eé] es (este|esta|el|la)\s+{_SUBJECT}\b",
    rf"\bexpl[ií]ca(me)? (este|esta|el|la)\s+{_SUBJECT}\b",
    rf"\bresumen (general |breve )?(de|del) (este |esta |el |la )?{_SUBJECT}\b",
    r"\b(visi[oó]n|descripci[oó]n|panorama) general\b",
    r"\bc[oó]mo est[aá] (organizad[oa]|estructurad[oa]|compuest[oa]|armad[oa]|dividid[oa])\b",
    rf"\b(estructura|organizaci[oó]n|arquitectura) (general )?(de|del) (este |esta |el |la )?{_SUBJECT}\b",
    r"\b(arquitectura|estructura) general\b",
    r"\b(cu[aá]les|qu[eé]) son (los|las) (principales|m[aá]s importantes)\s+(componentes|m[oó]dulos|servicios|carpetas|partes|piezas)\b",
    r"\bqu[eé] (servicios|m[oó]dulos|componentes|partes|carpetas) (hay|tiene|existen|componen|forman)\b",
    r"\b(qu[eé]|cu[aá]les) (tecnolog[ií]as|lenguajes|frameworks|librer[ií]as) (usa|utiliza|emplea|hay|tiene)\b",
    r"\bwhat does (this|the|that) (\w+ )?" + _SUBJECT + r" do\b",
    r"\bwhat is (this|the) " + _SUBJECT + r"( for| about)?\b",
    r"\bwhat('s| is) (this|the) " + _SUBJECT + r" about\b",
    r"\bhow is (this|the|it)( " + _SUBJECT + r")? (organi[sz]ed|structured|laid out|built up)\b",
    r"\b(high[- ]level|general) (overview|architecture|structure|description)\b",
    r"\boverview of (this|the) " + _SUBJECT + r"\b",
    r"\b(main|key|principal) (components|modules|services|parts|folders)\b",
    r"\bwhich (technologies|languages|frameworks) (does|do|are)\b",
]
_GENERAL_RE = re.compile("|".join(f"(?:{p})" for p in _GENERAL), re.IGNORECASE)


def is_general_question(question: str, identifiers=()) -> bool:
    """True for open questions about the whole repository. A question that names a code identifier is about
    specific code, even if it is phrased in general terms."""
    if identifiers:
        return False
    return bool(_GENERAL_RE.search(question))
