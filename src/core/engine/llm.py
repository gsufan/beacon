"""Llamadas al LLM con un contexto explícito y un presupuesto de tokens.

Por qué existe: sin `num_ctx`, Ollama carga el modelo con una ventana por
defecto (4.096 tokens en la versión usada) aunque llama3:8b admite 8.192, y
cuando el prompt no cabe **descarta el comienzo sin avisar** — justo donde
están las reglas del prompt de sistema. Medido con el repo psf/requests: una
consulta con top_k=10 generaba un prompt de 6.525 tokens y el modelo solo
procesaba 2.060. Este módulo:

1. fija `num_ctx` en cada llamada;
2. ofrece un presupuesto (cuántos tokens de contexto caben dejando espacio
   para la respuesta) para que quien arma el prompt recorte *antes* de enviar;
3. verifica después, con `prompt_eval_count`, que el modelo procesó todo lo
   enviado y registra una advertencia si no fue así.
"""

import logging
import math
from dataclasses import dataclass

logger = logging.getLogger("beacon.llm")

# Ventana máxima de llama3:8b. Si se cambia a un modelo con más contexto
# (ej. llama3.1), se puede subir; con uno de menos, bajar.
LLM_NUM_CTX = 8192

# Estimación conservadora de caracteres por token para código y español.
# Medido con llama3 sobre prompts reales de Beacon: ~4,3 caracteres/token;
# se usa 3,5 para dejar margen y no quedarse corto nunca.
CHARS_PER_TOKEN = 3.5


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def prompt_budget_tokens(reserve_for_answer: int) -> int:
    """Tokens disponibles para el prompt completo (sistema + usuario)."""
    return LLM_NUM_CTX - reserve_for_answer


@dataclass
class ChatResult:
    content: str
    prompt_tokens: int
    answer_tokens: int


def chat(client, model: str, system: str, user: str) -> ChatResult:
    """Una llamada de chat con `num_ctx` fijo. Advierte si el prompt no entró
    completo (el modelo procesó menos tokens de los que se estiman enviados)
    o si la respuesta llenó la ventana (pudo quedar cortada)."""
    response = client.chat(
        model=model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        options={"num_ctx": LLM_NUM_CTX},
    )
    prompt_tokens = response.get("prompt_eval_count") or 0
    answer_tokens = response.get("eval_count") or 0
    # El estimador es conservador (sobreestima), así que un prompt completo
    # siempre queda por debajo de la estimación; uno truncado queda muy por
    # debajo de la mitad. Umbral holgado para no dar falsas alarmas.
    expected = estimate_tokens(system) + estimate_tokens(user)
    if prompt_tokens and prompt_tokens < expected * 0.5:
        logger.warning("El modelo procesó %d tokens de un prompt estimado en %d: probablemente se truncó.",
                       prompt_tokens, expected)
    if prompt_tokens + answer_tokens >= LLM_NUM_CTX:
        logger.warning("La respuesta llenó la ventana de contexto (%d tokens): puede haber quedado incompleta.",
                       LLM_NUM_CTX)
    return ChatResult(response["message"]["content"], prompt_tokens, answer_tokens)
