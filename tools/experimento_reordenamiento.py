"""Experimento: reordenar los candidatos de la búsqueda con el LLM.

Pregunta que responde: ¿conviene una segunda etapa que pida a llama3 ordenar
los 20 candidatos de la búsqueda antes de armar el contexto? Es la forma de
reordenamiento disponible en local (Ollama no ofrece modelos de
reordenamiento propiamente tales). Se mide con los mismos conjuntos que
tools/eval_retrieval.py y se compara contra la búsqueda sin reordenar.

No forma parte del motor: si el resultado no justifica el costo (una llamada
al LLM más por consulta), queda como evidencia de la decisión.

Uso:
  python tools/experimento_reordenamiento.py <project_id> eval/requests.yaml
"""

import argparse
import re
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import yaml  # noqa: E402

from core.config import load_config  # noqa: E402
from core.engine.llm import chat  # noqa: E402
from core.engine.rag_engine import RAGEngine  # noqa: E402
from core.projects import get_project  # noqa: E402

CANDIDATES = 20
PREVIEW_LINES = 12
SYSTEM = ("Ordenas fragmentos de código según su utilidad para responder una pregunta. "
          "Respondes solo con números separados por comas, sin texto adicional.")


def rerank(engine, question, candidates):
    blocks = []
    for i, c in enumerate(candidates, 1):
        preview = "\n".join(c.code.splitlines()[:PREVIEW_LINES])
        blocks.append(f"[{i}] {c.file_path} — {c.name}\n{preview}")
    user = (f"PREGUNTA: {question}\n\nFRAGMENTOS:\n\n" + "\n\n".join(blocks)
            + "\n\nEscribe los números de los 5 fragmentos más útiles para responder la pregunta, "
              "del más útil al menos útil, separados por comas.")
    answer = chat(engine.client_ollama, engine.ai_config.llm_model, SYSTEM, user).content
    order = []
    for n in re.findall(r"\d+", answer):
        idx = int(n) - 1
        if 0 <= idx < len(candidates) and idx not in order:
            order.append(idx)
    rest = [i for i in range(len(candidates)) if i not in order]
    return [candidates[i] for i in order + rest]


def rank_of(chunks, expected):
    keys = [f"{c.file_path}::{c.name}" for c in chunks[:10]]
    return next((i for i, k in enumerate(keys, 1) if k in expected), None)


def summary(ranks):
    n = len(ranks)
    hits = {k: round(100 * sum(1 for r in ranks if r and r <= k) / n, 1) for k in (1, 3, 5)}
    mrr = round(sum(1 / r for r in ranks if r) / n, 3)
    return f"hit@1={hits[1]}%  hit@3={hits[3]}%  hit@5={hits[5]}%  MRR@10={mrr}"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("project_id")
    parser.add_argument("dataset")
    args = parser.parse_args()

    questions = yaml.safe_load(Path(args.dataset).read_text(encoding="utf-8"))["questions"]
    engine = RAGEngine(get_project(args.project_id), load_config().ai_provider)
    base, reranked, latencies = [], [], []
    for item in questions:
        expected = set(item["expected"])
        candidates = engine._ranked_candidates(item["q"], CANDIDATES)[:CANDIDATES]
        t = time.perf_counter()
        ordered = rerank(engine, item["q"], candidates)
        latencies.append(time.perf_counter() - t)
        base.append(rank_of(candidates, expected))
        reranked.append(rank_of(ordered, expected))
        print(f"{str(base[-1]):>5} -> {str(reranked[-1]):<5} {item['q']}", flush=True)
    print(f"\nsin reordenar:  {summary(base)}")
    print(f"reordenado:     {summary(reranked)}")
    print(f"costo: {statistics.mean(latencies):.1f} s más por consulta ({len(questions)} preguntas)")


if __name__ == "__main__":
    main()
