"""Diagnóstico: ¿en qué posición del ranking real queda un chunk específico
para una pregunta dada? Útil para distinguir 'no está indexado' de
'está indexado pero rankea bajo'."""
import os, sys
from dotenv import load_dotenv
load_dotenv()
from rag_engine import RAGEngine

if len(sys.argv) < 2:
    print('Uso: python rank_check.py "tu pregunta"')
    sys.exit(1)

question = " ".join(sys.argv[1:])
engine = RAGEngine()

# Pedimos MUCHOS resultados (no solo el top_k normal) para ver el ranking completo
chunks = engine.retrieve(question, top_k=30)

print(f"Pregunta: {question}\n")
print(f"{'#':<4}{'archivo':<45}{'tipo':<10}{'nombre':<20}{'distancia'}")
print("-" * 95)
for i, c in enumerate(chunks, 1):
    marker = " <-- shippingservice" if "shippingservice" in c.file_path and "genproto" not in c.file_path else ""
    print(f"{i:<4}{c.file_path:<45}{c.chunk_type:<10}{c.name:<20}{c.distance:.4f}{marker}")
