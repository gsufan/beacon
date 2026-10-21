"""Agrega los archivos de resultados del modo estudio (`beacon study`).

  python tools/study_analysis.py <carpeta con .json> [--csv respuestas.csv --columna "Pegue aquí..."]

Valida cada archivo (mismas reglas que `core.study.validate`), descarta los inválidos y duplicados, y
resume: tiempo de respuesta por tipo de GPU/VRAM, porcentaje de respuestas correctas, utilidad de las
fuentes y experiencia. También acepta la exportación CSV de un formulario donde cada participante pegó su
JSON en una columna. Nunca lee archivos de identidad."""

import argparse
import csv
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.study.validate import validate_result  # noqa: E402


def load_files(folder: Path) -> list:
    out = []
    for p in sorted(folder.glob("*.json")):
        if "identidad" in p.name:
            continue
        try:
            out.append((p.name, json.loads(p.read_text(encoding="utf-8"))))
        except json.JSONDecodeError:
            out.append((p.name, None))
    return out


def load_csv(path: Path, column: str) -> list:
    out = []
    with path.open(encoding="utf-8-sig", newline="") as f:
        for i, row in enumerate(csv.DictReader(f), 1):
            try:
                out.append((f"fila {i}", json.loads(row.get(column, ""))))
            except json.JSONDecodeError:
                out.append((f"fila {i}", None))
    return out


def vram_bucket(data: dict) -> str:
    gpus = (data.get("hardware") or {}).get("gpus") or []
    if not gpus:
        return "sin GPU / no declarada"
    v = max(g.get("vram_gb", 0) for g in gpus)
    return "<=6 GB" if v <= 6 else "7-8 GB" if v <= 8 else ">8 GB"


def summarize(entries: list) -> dict:
    valid, rejected, seen = [], [], set()
    for name, data in entries:
        if data is None:
            rejected.append((name, ["JSON ilegible"]))
            continue
        problems, warnings = validate_result(data)
        if problems:
            rejected.append((name, problems))
            continue
        digest = data["integridad"]["sha256_de_preguntas"]
        if digest in seen:
            rejected.append((name, ["duplicado"]))
            continue
        seen.add(digest)
        valid.append((name, data, warnings))

    times, correct, sources = defaultdict(list), defaultdict(int), []
    by_bucket = defaultdict(list)
    for _, data, _ in valid:
        for q in data["preguntas"]:
            times["todos"].append(q["segundos_respuesta"])
            by_bucket[vram_bucket(data)].append(q["segundos_respuesta"])
            v = q.get("valoracion")
            if v:
                correct[v["correcta"]] += 1
                if v.get("fuentes_utiles"):
                    sources.append(v["fuentes_utiles"])
    rated = sum(correct.values())

    def stats(xs):
        if not xs:
            return None
        xs = sorted(xs)
        return {"n": len(xs), "mediana": round(statistics.median(xs), 2), "p90": round(xs[int(0.9 * (len(xs) - 1))], 2)}

    return {
        "archivos": len(entries), "validos": len(valid), "rechazados": rejected,
        "con_avisos": [(n, w) for n, _, w in valid if w],
        "preguntas": len(times["todos"]), "tiempo_s": stats(times["todos"]),
        "tiempo_por_vram": {k: stats(v) for k, v in sorted(by_bucket.items())},
        "valoradas": rated,
        "correctas": {k: round(100 * v / rated, 1) for k, v in correct.items()} if rated else {},
        "utilidad_fuentes_media": round(statistics.mean(sources), 2) if sources else None,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("carpeta", nargs="?")
    ap.add_argument("--csv")
    ap.add_argument("--columna", default="")
    a = ap.parse_args()
    entries = []
    if a.carpeta:
        entries += load_files(Path(a.carpeta))
    if a.csv:
        entries += load_csv(Path(a.csv), a.columna)
    if not entries:
        ap.error("indica una carpeta con .json o --csv y --columna")
    print(json.dumps(summarize(entries), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
