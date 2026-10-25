"""Evaluates the full RAG answers (retrieval + LLM) with the real model.

It complements eval_retrieval.py: that one measures whether the right chunk reaches the model; this one
measures what the model does with it. The criteria are verifiable without human judgment, so the
measurement can be repeated and compared:

  - Content: the answer mentions the key facts of the question (`must` groups of the set; each group
    accepts alternatives).
  - Citation: it mentions the file where the answer is (prompt rule 3).
  - Correct refusal: for a question about something not in the repository, it says there is not enough
    information (rule 2) and adds no filler code (rule 4). A refusal followed by a generic example counts
    as incorrect.
  - False refusal: it says there is no information for an answerable question.
  - Ungrounded identifiers: code names the answer writes in backticks that appear in no chunk given to
    the model. A signal of possible invention, not proof: it may name, for example, a Python standard module.
  - Language: the answer is in Spanish.
  - Prompt size: the maximum tokens Ollama reports having processed, to confirm everything fits in the
    8,192 window (llm.chat also warns in the log if it were truncated).
  - Latency of the full answer.

Usage:
  python tools/eval_answers.py <project_id> eval/answers-requests.yaml [--json output.json]
"""

import argparse
import json
import re
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import yaml  # noqa: E402

from core.config import load_config  # noqa: E402
from core.engine.rag_engine import RAGEngine  # noqa: E402
from core.projects import get_project  # noqa: E402

# Refusal = the phrase required by prompt rule 2 (anywhere: if it closes a correct answer it is an
# undue refusal) or a refusal in other words at the start of the answer. A partial mention in the middle
# ("no information in the context about getproxies", which rule 6 allows) is not a refusal.
CANONICAL_REFUSAL = re.compile(r"no encontr[ée] informaci[óo]n suficiente", re.IGNORECASE)
REFUSAL = re.compile(
    r"no (encontr[ée]|hay|tengo|se encontr[óo]|dispongo de) (suficiente )?(informaci[óo]n|fragmentos|c[óo]digo)",
    re.IGNORECASE)
REFUSAL_OPENING_CHARS = 120


def is_refusal(answer: str) -> bool:
    if CANONICAL_REFUSAL.search(answer):
        return True
    match = REFUSAL.search(answer)
    return bool(match) and match.start() < REFUSAL_OPENING_CHARS
BACKTICKED = re.compile(r"`([^`\n]{2,80})`")
IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
SPANISH_WORDS = {"el", "la", "los", "las", "de", "que", "se", "en", "una", "un", "es", "para", "con", "por", "del"}
ENGLISH_WORDS = {"the", "is", "and", "of", "to", "in", "that", "this", "for", "with", "it", "are"}


def ungrounded_identifiers(answer: str, sources) -> list:
    corpus = "\n".join(f"{s.file_path}\n{s.code}" for s in sources)
    found = set()
    for snippet in BACKTICKED.findall(answer):
        for ident in IDENTIFIER.findall(snippet):
            if ident not in corpus:
                found.add(ident)
    return sorted(found)


def is_spanish(answer: str) -> bool:
    words = re.findall(r"[a-záéíóúñ]+", answer.lower())
    return sum(w in SPANISH_WORDS for w in words) > sum(w in ENGLISH_WORDS for w in words)


def grade(item: dict, answer: str, sources) -> dict:
    lowered = answer.lower()
    refused = is_refusal(answer)
    row = {"q": item["q"], "answerable": item.get("answerable", True), "refused": refused,
           "spanish": is_spanish(answer), "ungrounded": ungrounded_identifiers(answer, sources)}
    if not row["answerable"]:
        row["clean_refusal"] = refused and "```" not in answer
    else:
        missing = [group for group in item.get("must", [])
                   if not any(term.lower() in lowered for term in group)]
        row["missing"] = missing
        row["content_ok"] = not missing
        row["cites_ok"] = item.get("cites", "").lower() in lowered
    return row


def evaluate(project_id: str, questions: list) -> dict:
    engine = RAGEngine(get_project(project_id), load_config().ai_provider)
    rows = []
    for i, item in enumerate(questions, 1):
        print(f"[{i}/{len(questions)}] {item['q']}", file=sys.stderr, flush=True)
        t = time.perf_counter()
        response = engine.ask(item["q"])
        latency = time.perf_counter() - t
        row = grade(item, response.answer, response.sources)
        row.update({"answer": response.answer, "latency_s": round(latency, 1),
                    "prompt_tokens": response.prompt_tokens, "answer_tokens": response.answer_tokens,
                    "sources": len(response.sources)})
        rows.append(row)

    answerable = [r for r in rows if r["answerable"]]
    unanswerable = [r for r in rows if not r["answerable"]]

    def pct(part, total):
        return round(100 * part / total, 1) if total else None

    summary = {
        "contenido": pct(sum(r["content_ok"] for r in answerable), len(answerable)),
        "cita": pct(sum(r["cites_ok"] for r in answerable), len(answerable)),
        "contenido_y_cita": pct(sum(r["content_ok"] and r["cites_ok"] for r in answerable), len(answerable)),
        "falso_rechazo": pct(sum(r["refused"] for r in answerable), len(answerable)),
        "rechazo_correcto": pct(sum(r["clean_refusal"] for r in unanswerable), len(unanswerable)),
        "sin_identificadores_dudosos": pct(sum(not r["ungrounded"] for r in rows), len(rows)),
        "en_espanol": pct(sum(r["spanish"] for r in rows), len(rows)),
        "latencia_media_s": round(statistics.mean(r["latency_s"] for r in rows), 1),
        "tokens_prompt_max": max(r["prompt_tokens"] for r in rows),
        "respondibles": len(answerable),
        "no_respondibles": len(unanswerable),
    }
    return {"summary": summary, "rows": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("project_id")
    parser.add_argument("dataset")
    parser.add_argument("--json", help="Guarda el detalle (incluidas las respuestas) en este archivo.")
    args = parser.parse_args()

    questions = yaml.safe_load(Path(args.dataset).read_text(encoding="utf-8"))["questions"]
    result = evaluate(args.project_id, questions)

    for r in result["rows"]:
        if r["answerable"]:
            marks = ("contenido" if r["content_ok"] else "FALTA CONTENIDO",
                     "cita" if r["cites_ok"] else "SIN CITA")
            if r["refused"]:
                marks += ("RECHAZO INDEBIDO",)
        else:
            marks = ("rechazo correcto" if r["clean_refusal"] else
                     "RECHAZÓ PERO AGREGÓ CÓDIGO" if r["refused"] else "NO RECHAZÓ",)
        if not r["spanish"]:
            marks += ("NO ESPAÑOL",)
        print(f"{r['latency_s']:>5}s  {' | '.join(marks)}  {r['q']}")
        if r.get("missing"):
            print(f"{'':>8}falta mencionar: {r['missing']}")
        if r["ungrounded"]:
            print(f"{'':>8}identificadores sin respaldo en el contexto: {', '.join(r['ungrounded'])}")
    s = result["summary"]
    print(f"\nRespondibles ({s['respondibles']}): contenido {s['contenido']}%, cita {s['cita']}%, "
          f"ambos {s['contenido_y_cita']}%, falso rechazo {s['falso_rechazo']}%")
    print(f"No respondibles ({s['no_respondibles']}): rechazo correcto {s['rechazo_correcto']}%")
    print(f"Todas: sin identificadores dudosos {s['sin_identificadores_dudosos']}%, en español {s['en_espanol']}%, "
          f"latencia media {s['latencia_media_s']}s, prompt máximo {s['tokens_prompt_max']} tokens")
    if args.json:
        Path(args.json).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
