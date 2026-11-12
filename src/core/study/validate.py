"""Automatic validation of a results file on receipt. Returns problems (the file is unusable) and warnings (usable but suspicious)."""

from typing import List, Tuple

from core.study.session import CORRECT_VALUES, MIN_QUESTIONS, PROFILES, SCHEMA_VERSION

REQUIRED = ("schema", "meta", "consents", "questions", "integrity")


def validate_result(data: dict) -> Tuple[List[str], List[str]]:
    problems, warnings = [], []
    if not isinstance(data, dict):
        return ["el archivo no es un objeto JSON"], []
    for key in REQUIRED:
        if key not in data:
            problems.append(f"falta '{key}'")
    if problems:
        return problems, warnings
    if data["schema"] != SCHEMA_VERSION:
        problems.append(f"esquema {data['schema']} no soportado")
    if data["meta"].get("profile") not in PROFILES:
        problems.append("perfil inválido")
    if not any(c.get("kind") == "participacion" and c.get("accepted") for c in data["consents"]):
        problems.append("sin consentimiento de participación")
    questions = data["questions"]
    if len(questions) < MIN_QUESTIONS:
        problems.append(f"menos de {MIN_QUESTIONS} preguntas")
    for q in questions:
        v = q.get("rating")
        if v and v.get("correct") not in CORRECT_VALUES:
            problems.append(f"valoración inválida en la pregunta {q.get('n')}")
        if not str(q.get("question", "")).strip() or not str(q.get("answer", "")).strip():
            problems.append(f"pregunta o respuesta vacía en la pregunta {q.get('n')}")
    if problems:
        return problems, warnings
    texts = [q["question"].strip().lower() for q in questions]
    if len(set(texts)) < len(texts):
        warnings.append("preguntas repetidas")
    times = [q.get("answer_seconds", 0) for q in questions]
    if any(t <= 0.2 for t in times):
        warnings.append("tiempo de respuesta imposible (menor o igual a 0,2 s)")
    if max(times) > 900:
        warnings.append("tiempo de respuesta mayor a 15 minutos")
    if all(q.get("rating") is None for q in questions):
        warnings.append("ninguna respuesta fue valorada")
    # A short refusal ("no information found") can come back in under 1 s; what is implausible is generating
    # long answers above 400 tokens/s with a local 8B model.
    if any(q.get("answer_tokens", 0) >= 50 and q.get("answer_seconds", 0) > 0
           and q["answer_tokens"] / q["answer_seconds"] > 400 for q in questions):
        warnings.append("velocidad de generación de más de 400 tokens/s: poco plausible para un modelo local de 8B")
    return problems, warnings
