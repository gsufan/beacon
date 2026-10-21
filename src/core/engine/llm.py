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
import time
from dataclasses import dataclass
from typing import Callable, Optional

from core.config import DEFAULT_LLM_NUM_CTX

logger = logging.getLogger("beacon.llm")

# Ventana por defecto: el máximo de llama3:8b. Con un modelo de más contexto
# (ej. llama3.1 o uno de 32B) se sube con `llm_num_ctx` en config.yaml; con uno
# de menos, se baja. La memoria de video que pide la ventana crece con su tamaño.
LLM_NUM_CTX = DEFAULT_LLM_NUM_CTX

# Respuestas sobre código: se prefieren precisas y repetibles antes que
# variadas. Con la temperatura por defecto de Ollama (0,8) la misma pregunta
# daba respuestas distintas entre corridas (medido con tools/eval_answers.py:
# el contenido correcto variaba entre 67% y 92%). Temperatura baja y semilla
# fija hacen que la misma pregunta con el mismo contexto dé la misma respuesta.
LLM_TEMPERATURE = 0.2
LLM_SEED = 42

# Estimación conservadora de caracteres por token para código y español.
# Medido con llama3 sobre prompts reales de Beacon: ~4,3 caracteres/token;
# se usa 3,5 para dejar margen y no quedarse corto nunca.
CHARS_PER_TOKEN = 3.5

# Tope de tokens de una respuesta. Sin él, un modelo que cae en un bucle de repetición genera hasta llenar la
# ventana (minutos con la GPU al 100 %). Coincide con el espacio que se reserva para la respuesta al armar el prompt.
LLM_MAX_ANSWER_TOKENS = 1024


class LLMStoppedError(RuntimeError):
    """La generación se detuvo antes de terminar."""


class LLMTimeoutError(LLMStoppedError):
    """La respuesta tardó más del tiempo máximo permitido."""


class LLMCancelledError(LLMStoppedError):
    """Quien preguntó (o el cierre del servidor) pidió detener la respuesta."""


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def prompt_budget_tokens(reserve_for_answer: int, num_ctx: int = LLM_NUM_CTX) -> int:
    """Tokens disponibles para el prompt completo (sistema + usuario)."""
    return num_ctx - reserve_for_answer


@dataclass
class ChatResult:
    content: str
    prompt_tokens: int
    answer_tokens: int
    truncated: bool = False  # la respuesta llegó al tope de longitud y quedó cortada


def _stream_chat(client, deadline: Optional[float], should_stop: Optional[Callable[[], bool]], **kwargs) -> dict:
    """Chat en streaming para poder detenerlo: entre fragmentos revisa el tiempo máximo y la cancelación, y cerrar
    el flujo corta la conexión con Ollama (que deja de generar)."""
    parts, last = [], {}
    stream = client.chat(stream=True, **kwargs)
    try:
        for chunk in stream:
            parts.append((chunk.get("message") or {}).get("content", ""))
            if chunk.get("done"):
                last = chunk
            if should_stop is not None and should_stop():
                raise LLMCancelledError("La respuesta se canceló.")
            if deadline is not None and time.monotonic() > deadline:
                raise LLMTimeoutError("La respuesta tardó más del tiempo máximo y se detuvo.")
    finally:
        close = getattr(stream, "close", None)
        if close:
            close()
    return {"message": {"content": "".join(parts)}, "prompt_eval_count": last.get("prompt_eval_count"),
            "eval_count": last.get("eval_count")}


def chat(client, model: str, system: str, user: str, num_ctx: int = LLM_NUM_CTX,
         max_tokens: int = LLM_MAX_ANSWER_TOKENS, max_seconds: Optional[float] = None,
         should_stop: Optional[Callable[[], bool]] = None) -> ChatResult:
    """Una llamada de chat con `num_ctx` explícito. Advierte si el prompt no entró
    completo (el modelo procesó menos tokens de los que se estiman enviados)
    o si la respuesta llenó la ventana (pudo quedar cortada). La respuesta tiene un tope de `max_tokens`;
    con `max_seconds` o `should_stop` se genera en streaming y se puede detener (LLMStoppedError)."""
    kwargs = dict(
        model=model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        options={"num_ctx": num_ctx, "temperature": LLM_TEMPERATURE, "seed": LLM_SEED, "num_predict": max_tokens},
    )
    if max_seconds is not None or should_stop is not None:
        deadline = time.monotonic() + max_seconds if max_seconds is not None else None
        response = _stream_chat(client, deadline, should_stop, **kwargs)
    else:
        response = client.chat(**kwargs)
    prompt_tokens = response.get("prompt_eval_count") or 0
    answer_tokens = response.get("eval_count") or 0
    # El estimador es conservador (sobreestima), así que un prompt completo
    # siempre queda por debajo de la estimación; uno truncado queda muy por
    # debajo de la mitad. Umbral holgado para no dar falsas alarmas.
    expected = estimate_tokens(system) + estimate_tokens(user)
    if prompt_tokens and prompt_tokens < expected * 0.5:
        logger.warning("El modelo procesó %d tokens de un prompt estimado en %d: probablemente se truncó.",
                       prompt_tokens, expected)
    if prompt_tokens + answer_tokens >= num_ctx:
        logger.warning("La respuesta llenó la ventana de contexto (%d tokens): puede haber quedado incompleta.",
                       num_ctx)
    truncated = bool(answer_tokens) and answer_tokens >= max_tokens
    if truncated:
        logger.warning("La respuesta llegó al tope de %d tokens y se cortó (¿bucle de repetición?).", max_tokens)
    return ChatResult(response["message"]["content"], prompt_tokens, answer_tokens, truncated)
