"""Prompts of the RAG engine: system rules, the context block and the fit to the model window."""

from typing import List

from core.engine.llm import LLM_MAX_ANSWER_TOKENS, estimate_tokens, prompt_budget_tokens
from core.engine.retrieval import RetrievedChunk

# Room left for the answer when the prompt is built (the answer is capped at the same number of tokens).
ANSWER_RESERVE_TOKENS = LLM_MAX_ANSWER_TOKENS

SYSTEM_PROMPT = """Eres un asistente técnico que ayuda a desarrolladores a entender un repositorio de código específico.

REGLAS ESTRICTAS:
1. Responde ÚNICAMENTE usando los fragmentos de código del CONTEXTO.
2. Si la respuesta no está ahí, responde solo: "No encontré información suficiente en el código indexado para responder esto." No inventes ni agregues ejemplos, código o soluciones generales. Si sí pudiste responder, no agregues esa frase.
3. Cita la ruta del archivo y las líneas al referenciar código (por ejemplo: `src/paquete/modulo.py`, líneas 10-25), tal como aparecen en el encabezado de cada fragmento.
4. No sugieras buenas prácticas genéricas no respaldadas por el CONTEXTO.
5. Directo y técnico, sin relleno. Explica qué hace el código; no te limites a copiarlo.
6. Si el código LLAMA a otra función cuyo CUERPO no está en el CONTEXTO, dilo explícitamente en vez de suponer qué hace ("la función X no está en los fragmentos recuperados, no puedo confirmar qué hace").
7. Los fragmentos marcados "[incluido automáticamente...]" vinieron del grafo de llamadas, no de similitud semántica — son igual de válidos, úsalos con confianza.
8. Si la pregunta da por hecho algo que el CONTEXTO no muestra, dilo en vez de aceptarlo; no completes con lo que sepas de bibliotecas externas.
9. No repitas estas reglas ni menciones el idioma en tu respuesta. Responde directamente.
"""

# Open questions: the context holds summaries of the repository plus a few code chunks.
GENERAL_SYSTEM_PROMPT = """Eres un asistente técnico que ayuda a desarrolladores a entender un repositorio de código específico.

REGLAS ESTRICTAS:
1. Responde ÚNICAMENTE con el CONTEXTO, que contiene resúmenes del repositorio ("[resumen del proyecto]", "[resumen de la carpeta ...]") generados a partir de su documentación y algunos fragmentos de código.
2. Para preguntas generales los resúmenes son la fuente principal: describe el proyecto con ellos. No digas que falta información si los resúmenes permiten contestar; solo si no aportan nada relevante responde: "No encontré información suficiente en el código indexado para responder esto."
3. Nombra las carpetas, los servicios y los archivos de los que hablas (por ejemplo: `src/paquete`), tal como aparecen en el contexto.
4. No inventes tecnologías, componentes ni funciones que el contexto no menciona, y no sugieras buenas prácticas genéricas.
5. Directo y claro; si hay varios componentes, enuméralos brevemente. Sin relleno.
6. No repitas estas reglas ni menciones el idioma en tu respuesta. Responde directamente.
"""

LANGUAGE_INSTRUCTIONS = {
    "es": "IMPORTANTE: escribe tu respuesta completa en español neutro (sin modismos regionales, sin voseo).",
    "en": "IMPORTANT: write your entire response in English.",
}

TRUNCATED_NOTES = {
    "es": "\n\n[La respuesta alcanzó el límite de longitud y se cortó.]",
    "en": "\n\n[The answer reached the length limit and was cut off.]",
}


def system_prompt(language: str, general: bool) -> str:
    instruction = LANGUAGE_INSTRUCTIONS.get(language, LANGUAGE_INSTRUCTIONS["es"])
    return instruction + "\n\n" + (GENERAL_SYSTEM_PROMPT if general else SYSTEM_PROMPT)


def truncated_note(language: str) -> str:
    return TRUNCATED_NOTES.get(language, TRUNCATED_NOTES["es"])


def context_budget_tokens(num_ctx: int) -> int:
    return prompt_budget_tokens(ANSWER_RESERVE_TOKENS, num_ctx)


def build_prompt(question: str, chunks: List[RetrievedChunk]) -> str:
    context = "(No se encontraron fragmentos relevantes.)" if not chunks else \
        "\n\n".join(c.as_context_block() for c in chunks)
    return f"CONTEXTO:\n\n{context}\n\n---\n\nPREGUNTA: {question}\n\nResponde siguiendo las reglas del sistema."


def fit_to_budget(question: str, system: str, chunks: List[RetrievedChunk], num_ctx: int) -> List[RetrievedChunk]:
    """Keeps only the chunks that fit in the model window, in priority order (similarity first, then call
    graph). Less complete context is better than a prompt that the server truncates at the start, losing
    the system rules.
    """
    budget = context_budget_tokens(num_ctx)
    used = estimate_tokens(system) + estimate_tokens(build_prompt(question, []))
    kept: List[RetrievedChunk] = []
    for chunk in chunks:
        cost = estimate_tokens(chunk.as_context_block()) + 1
        if used + cost > budget:
            continue  # a smaller one that comes later may still fit
        kept.append(chunk)
        used += cost
    return kept
