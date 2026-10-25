[English](../en/development.md) | **Español**

# Desarrollo

## Estructura del código

```
config/config.yaml       # proveedor de IA + registro de proyectos (multi-repo)
src/core/
  cli.py                  # comando `beacon`
  api.py                  # API REST (FastAPI) + sirve la UI compilada
  engine/
    backends/              # servidores de modelos (hoy Ollama) tras una interfaz común
    chunker.py             # chunking AST políglota
    git_watcher.py          # detección incremental de cambios + exclusión de código generado
    indexer.py              # embeddings + ChromaDB
    doc_generator.py        # documentación .md incremental
    rag_engine.py            # retrieval + expansión por grafo de llamadas + LLM
frontend/                # UI (React + Vite + Tailwind)
tools/                   # scripts de diagnóstico
```

## Backends de modelos

El motor nunca habla directamente con un servidor de modelos: usa un `Backend` (`src/core/engine/backends/`) que expone `chat`, `embed`, `list_models` y `health`, y lanza sus propios errores (`BackendUnavailableError`, `ModelNotFoundError`, `InputRejectedError`). Hoy el único adaptador es el de Ollama. Para agregar un servidor se escribe un adaptador que traduzca su protocolo y sus errores a ese contrato y se registra en `backends/__init__.py`; las claves `provider` y `host` de `ai_provider` en `config.yaml` lo seleccionan. Los backends deben ejecutar los modelos en infraestructura controlada por la organización.

Cada proyecto indexado vive aislado en `data/<project_id>/` (su propio índice ChromaDB y su propia documentación generada): varios repositorios conviven sin pisarse.

## Frontend con recarga en caliente

Para trabajar en la UI con recarga en caliente:

```bash
# Terminal 1
beacon serve

# Terminal 2
cd frontend
npm run dev   # http://localhost:5173, con proxy hacia la API en :8000
```

## Pruebas

```bash
pip install -r requirements-dev.txt   # una vez: agrega pytest
pytest tests/              # backend

cd frontend
npm test                   # frontend (Vitest + Testing Library)
```
