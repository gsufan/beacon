"""LLM calls with an explicit context window and a token budget.

Without an explicit `num_ctx`, servers such as Ollama load the model with a default window and silently
drop the start of the prompt when it does not fit, which is where the system rules are. This module sets
`num_ctx` on every call, computes how many context tokens fit while leaving room for the answer (so the
prompt builder trims before sending), and checks with the processed-token count that the whole prompt
was used.
"""

import logging
import math
from dataclasses import dataclass
from typing import Callable, Optional

from core.config import DEFAULT_LLM_NUM_CTX
from core.engine.backends import Backend, LLMCancelledError, LLMStoppedError, LLMTimeoutError  # noqa: F401 (re-exported)

logger = logging.getLogger("beacon.llm")

# Default window: the maximum of llama3:8b. Change it with `llm_num_ctx` in config.yaml; the video
# memory needed grows with its size.
LLM_NUM_CTX = DEFAULT_LLM_NUM_CTX

# Answers about code must be precise and repeatable: low temperature and a fixed seed.
LLM_TEMPERATURE = 0.2
LLM_SEED = 42

# Conservative estimate of characters per token for code and Spanish (llama3 gives about 4.3).
CHARS_PER_TOKEN = 3.5

# Token cap for one answer, to stop repetition loops. It matches the space reserved for the answer when
# the prompt is built.
LLM_MAX_ANSWER_TOKENS = 1024


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def prompt_budget_tokens(reserve_for_answer: int, num_ctx: int = LLM_NUM_CTX) -> int:
    return num_ctx - reserve_for_answer


@dataclass
class ChatResult:
    content: str
    prompt_tokens: int
    answer_tokens: int
    truncated: bool = False  # the answer hit the length cap and was cut


def chat(backend: Backend, model: str, system: str, user: str, num_ctx: int = LLM_NUM_CTX,
         max_tokens: int = LLM_MAX_ANSWER_TOKENS, max_seconds: Optional[float] = None,
         should_stop: Optional[Callable[[], bool]] = None) -> ChatResult:
    """One chat call with an explicit `num_ctx`. Warns if the prompt did not fit (the model processed fewer
    tokens than estimated) or if the answer filled the window (it may be cut). The answer is capped at
    `max_tokens`; with `max_seconds` or `should_stop` it streams and can be stopped (LLMStoppedError).
    """
    raw = backend.chat(model, system, user, num_ctx=num_ctx, max_tokens=max_tokens, temperature=LLM_TEMPERATURE,
                       seed=LLM_SEED, max_seconds=max_seconds, should_stop=should_stop)
    prompt_tokens, answer_tokens = raw.prompt_tokens, raw.answer_tokens
    # The estimator overestimates: a complete prompt is below the estimate and a truncated one far below
    # half. The threshold is loose to avoid false alarms.
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
    return ChatResult(raw.content, prompt_tokens, answer_tokens, truncated)
