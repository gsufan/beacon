"""Diagnóstico: busca directamente en el índice cuántos chunks hay de un
archivo específico, y si existen, en qué posición/distancia habrían quedado
para una pregunta dada. Uso rápido, no requiere modificar nada del proyecto."""
import os, sys
sys.path.insert(0, '.')
from dotenv import load_dotenv
load_dotenv()
import chromadb

CHROMA_DIR = os.getenv("CHROMA_PERSIST_DIR", "./data/chroma_db")
client = chromadb.PersistentClient(path=CHROMA_DIR)
col = client.get_collection(os.getenv("CHROMA_COLLECTION_NAME", "codebase_index"))

# Contar cuántos chunks hay por "familia" de archivo, agrupando generado vs real
data = col.get(include=["metadatas"])
from collections import Counter
paths = [m.get("file_path", "?") for m in data["metadatas"]]
generated = [p for p in paths if "genproto" in p or "_pb2" in p or ".pb.go" in p or "_grpc.py" in p]
real_shipping = [p for p in paths if "shippingservice" in p and "genproto" not in p]

print(f"Total chunks en el índice: {len(paths)}")
print(f"Chunks de código GENERADO (pb2/grpc/genproto): {len(generated)}")
print(f"Chunks reales de shippingservice (no generados): {real_shipping}")
