"""Validación automática de un archivo de resultados al recibirlo (pensada para cientos de archivos).
Devuelve problemas (el archivo no sirve) y avisos (el archivo sirve pero es sospechoso)."""

from typing import List, Tuple

from core.study.session import CORRECT_VALUES, MIN_QUESTIONS, PROFILES, SCHEMA_VERSION

REQUIRED = ("esquema", "meta", "consentimientos", "preguntas", "integridad")


def validate_result(data: dict) -> Tuple[List[str], List[str]]:
    problems, warnings = [], []
    if not isinstance(data, dict):
        return ["el archivo no es un objeto JSON"], []
    for key in REQUIRED:
        if key not in data:
            problems.append(f"falta '{key}'")
    if problems:
        return problems, warnings
    if data["esquema"] != SCHEMA_VERSION:
        problems.append(f"esquema {data['esquema']} no soportado")
    if data["meta"].get("perfil") not in PROFILES:
        problems.append("perfil inválido")
    if not any(c.get("tipo") == "participacion" and c.get("aceptado") for c in data["consentimientos"]):
        problems.append("sin consentimiento de participación")
    questions = data["preguntas"]
    if len(questions) < MIN_QUESTIONS:
        problems.append(f"menos de {MIN_QUESTIONS} preguntas")
    for q in questions:
        v = q.get("valoracion")
        if v and v.get("correcta") not in CORRECT_VALUES:
            problems.append(f"valoración inválida en la pregunta {q.get('n')}")
        if not str(q.get("pregunta", "")).strip() or not str(q.get("respuesta", "")).strip():
            problems.append(f"pregunta o respuesta vacía en la pregunta {q.get('n')}")
    if problems:
        return problems, warnings
    texts = [q["pregunta"].strip().lower() for q in questions]
    if len(set(texts)) < len(texts):
        warnings.append("preguntas repetidas")
    times = [q.get("segundos_respuesta", 0) for q in questions]
    if any(t <= 0.2 for t in times):
        warnings.append("tiempo de respuesta imposible (menor o igual a 0,2 s)")
    if max(times) > 900:
        warnings.append("tiempo de respuesta mayor a 15 minutos")
    if all(q.get("valoracion") is None for q in questions):
        warnings.append("ninguna respuesta fue valorada")
    # Una negativa corta («no encontré información») puede salir en menos de 1 s: lo que no es plausible es generar
    # respuestas largas a más de 400 tokens/s con un modelo local de 8B.
    if any(q.get("tokens_respuesta", 0) >= 50 and q.get("segundos_respuesta", 0) > 0
           and q["tokens_respuesta"] / q["segundos_respuesta"] > 400 for q in questions):
        warnings.append("velocidad de generación de más de 400 tokens/s: poco plausible para un modelo local de 8B")
    return problems, warnings
