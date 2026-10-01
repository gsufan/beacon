"""Evalúa la recuperación del RAG contra un conjunto de preguntas de referencia.

Métricas (estándar en recuperación de información):
  - Hit@k (recall@k por pregunta): % de preguntas con al menos un fragmento
    esperado entre los k primeros resultados.
  - MRR@10: promedio de 1/posición del primer fragmento correcto (0 si no
    aparece en los 10 primeros). Premia que el correcto quede arriba.
  - En contexto: % de preguntas cuyo fragmento esperado queda dentro del
    contexto que realmente se entrega al modelo (top_k=5 más los fragmentos
    casi empatados que suma el contexto adaptativo), y su tamaño medio.
  - Latencia media de la recuperación (embedding de la pregunta + búsqueda),
    sin contar la generación del LLM.

Solo mide la recuperación (no llama al LLM), así que es rápido y repetible:
sirve para comparar cambios (ej. otro modelo de embeddings) con números.

Uso:
  python tools/eval_retrieval.py <project_id> eval/requests.yaml [--json salida.json]
"""

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import yaml  # noqa: E402

from core.config import load_config  # noqa: E402
from core.engine.rag_engine import RAGEngine  # noqa: E402
from core.projects import get_project  # noqa: E402

KS = (1, 3, 5, 10)


def evaluate(project_id: str, questions: list) -> dict:
    engine = RAGEngine(get_project(project_id), load_config().ai_provider)
    # Una clave esperada mal escrita contaría como fallo de la búsqueda sin
    # serlo: se aborta antes de medir.
    indexed = {f"{m['file_path']}::{m['name']}" for m in engine.collection.get(include=["metadatas"])["metadatas"]}
    unknown = sorted({k for item in questions for k in item["expected"]} - indexed)
    if unknown:
        raise SystemExit("Fragmentos esperados que no existen en el índice:\n  " + "\n  ".join(unknown))
    rows = []
    for item in questions:
        expected = set(item["expected"])
        t = time.perf_counter()
        chunks = engine.retrieve(item["q"], top_k=max(KS))
        latency = time.perf_counter() - t
        got = [f"{c.file_path}::{c.name}" for c in chunks]
        rank = next((i for i, key in enumerate(got, 1) if key in expected), None)
        context = [f"{c.file_path}::{c.name}" for c in engine.select_context(item["q"], top_k=5)]
        rows.append({"q": item["q"], "expected": sorted(expected), "rank": rank, "top3": got[:3],
                     "in_context": any(k in expected for k in context), "context_size": len(context),
                     "latency_s": round(latency, 3)})
    n = len(rows)
    summary = {f"hit@{k}": round(100 * sum(1 for r in rows if r["rank"] and r["rank"] <= k) / n, 1) for k in KS}
    summary["mrr@10"] = round(sum(1 / r["rank"] for r in rows if r["rank"]) / n, 3)
    summary["en_contexto"] = round(100 * sum(r["in_context"] for r in rows) / n, 1)
    summary["contexto_medio"] = round(statistics.mean(r["context_size"] for r in rows), 1)
    summary["latencia_media_s"] = round(statistics.mean(r["latency_s"] for r in rows), 3)
    summary["preguntas"] = n
    return {"summary": summary, "rows": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("project_id")
    parser.add_argument("dataset")
    parser.add_argument("--json", help="Guarda el detalle en este archivo (para comparar corridas).")
    args = parser.parse_args()

    questions = yaml.safe_load(Path(args.dataset).read_text(encoding="utf-8"))["questions"]
    result = evaluate(args.project_id, questions)

    for r in result["rows"]:
        mark = f"#{r['rank']}" if r["rank"] else "fuera del top 10"
        print(f"{mark:>17}  {r['q']}")
        if not r["rank"] or r["rank"] > 5:
            print(f"{'':>19}esperado: {', '.join(r['expected'])}")
            print(f"{'':>19}obtuvo:   {', '.join(r['top3'])}")
    s = result["summary"]
    print("\n" + "  ".join(f"{k}={v}%" for k, v in s.items() if k.startswith("hit@"))
          + f"  MRR@10={s['mrr@10']}  en contexto={s['en_contexto']}% (media {s['contexto_medio']} fragmentos)"
          + f"  latencia={s['latencia_media_s']}s  ({s['preguntas']} preguntas)")
    if args.json:
        Path(args.json).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
