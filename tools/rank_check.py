"""Ranking completo (no solo top-k) para una pregunta, útil para diagnosticar recuperación.
Uso: python tools/rank_check.py <project_id> "pregunta"'"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.config import load_config
from core.projects import get_project
from core.engine.rag_engine import RAGEngine


def rank_check(project_id: str, question: str):
    project = get_project(project_id)
    cfg = load_config()
    engine = RAGEngine(project, cfg.ai_provider)
    chunks = engine.retrieve(question, top_k=30)

    print(f"{'#':<4}{'archivo':<45}{'tipo':<10}{'nombre':<20}{'distancia'}")
    print("-" * 95)
    for i, c in enumerate(chunks, 1):
        print(f"{i:<4}{c.file_path:<45}{c.chunk_type:<10}{c.name:<20}{c.distance:.4f}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print('Uso: python tools/rank_check.py <project_id> "pregunta"')
        sys.exit(1)
    rank_check(sys.argv[1], " ".join(sys.argv[2:]))
