"""
Script de verificación de entorno.
Corre esto ANTES de escribir cualquier lógica del proyecto.
Valida: (1) Ollama responde, (2) el modelo de embeddings funciona,
(3) el LLM local responde, (4) ChromaDB puede persistir en disco.

Uso:
    python test_setup.py
"""

import os
import sys
from dotenv import load_dotenv

load_dotenv()

EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "nomic-embed-text")
LLM_MODEL = os.getenv("LLM_MODEL", "llama3:8b")
CHROMA_DIR = os.getenv("CHROMA_PERSIST_DIR", "./data/chroma_db")


def check_ollama():
    print("\n[1/4] Verificando conexión con Ollama...")
    try:
        import ollama
        models = ollama.list()
        names = [m["model"] for m in models.get("models", [])]
        print(f"    OK. Modelos instalados: {names}")
        return True
    except Exception as e:
        print(f"    FALLÓ. ¿Está 'ollama serve' corriendo? Detalle: {e}")
        return False


def check_embeddings():
    print(f"\n[2/4] Probando embeddings con '{EMBEDDING_MODEL}'...")
    try:
        import ollama
        resp = ollama.embeddings(model=EMBEDDING_MODEL, prompt="def hello_world(): pass")
        vector = resp["embedding"]
        print(f"    OK. Vector generado con dimensión: {len(vector)}")
        return True
    except Exception as e:
        print(f"    FALLÓ. ¿Corriste 'ollama pull {EMBEDDING_MODEL}'? Detalle: {e}")
        return False


def check_llm():
    print(f"\n[3/4] Probando generación con '{LLM_MODEL}'...")
    try:
        import ollama
        resp = ollama.chat(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": "Responde solo con: OK"}],
        )
        content = resp["message"]["content"].strip()
        print(f"    OK. Respuesta del modelo: {content[:80]}")
        return True
    except Exception as e:
        print(f"    FALLÓ. ¿Corriste 'ollama pull {LLM_MODEL}'? Detalle: {e}")
        return False


def check_chromadb():
    print(f"\n[4/4] Verificando ChromaDB (persistencia en '{CHROMA_DIR}')...")
    try:
        import chromadb
        client = chromadb.PersistentClient(path=CHROMA_DIR)
        collection = client.get_or_create_collection(name="setup_test")
        collection.add(
            ids=["test1"],
            documents=["función de prueba"],
            metadatas=[{"lang": "python"}],
        )
        result = collection.query(query_texts=["función de prueba"], n_results=1)
        print(f"    OK. Colección de prueba creada y consultada correctamente.")
        client.delete_collection("setup_test")
        return True
    except Exception as e:
        print(f"    FALLÓ. Detalle: {e}")
        return False


if __name__ == "__main__":
    print("=" * 60)
    print("VERIFICACIÓN DE ENTORNO — Plataforma RAG de Deuda Técnica")
    print("=" * 60)

    results = [check_ollama(), check_embeddings(), check_llm(), check_chromadb()]

    print("\n" + "=" * 60)
    if all(results):
        print("TODO LISTO. El entorno está preparado para el desarrollo.")
    else:
        print("Hay pasos pendientes. Revisa los mensajes FALLÓ arriba.")
        sys.exit(1)
