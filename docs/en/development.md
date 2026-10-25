[Español](../es/desarrollo.md) | **English**

# Development

## Code structure

```
config/config.yaml       # AI provider + project registry (multi-repo)
src/core/
  cli.py                  # `beacon` command
  api.py                  # REST API (FastAPI) + serves the compiled UI
  engine/
    backends/              # model servers (Ollama today) behind a common interface
    chunker.py             # polyglot AST chunking
    git_watcher.py          # incremental change detection + exclusion of generated code
    indexer.py              # embeddings + ChromaDB
    doc_generator.py        # incremental .md documentation
    rag_engine.py            # retrieval + call-graph expansion + LLM
frontend/                # UI (React + Vite + Tailwind)
tools/                   # diagnostic scripts
```

## Model backends

The engine never talks to a model server directly: it uses a `Backend` (`src/core/engine/backends/`) that exposes `chat`, `embed`, `list_models` and `health`, and raises its own errors (`BackendUnavailableError`, `ModelNotFoundError`, `InputRejectedError`). Ollama is the only adapter today. To add a server, write an adapter that translates its protocol and errors to that contract and register it in `backends/__init__.py`; the `provider` and `host` keys of `ai_provider` in `config.yaml` select it. Backends must run models on infrastructure controlled by the organization.

Each indexed project lives isolated in `data/<project_id>/` (its own ChromaDB index and its own generated documentation): several repositories coexist without overwriting each other.

## Frontend with hot reload

To work on the UI with hot reload:

```bash
# Terminal 1
beacon serve

# Terminal 2
cd frontend
npm run dev   # http://localhost:5173, proxied to the API on :8000
```

## Tests

```bash
pip install -r requirements-dev.txt   # once: adds pytest
pytest tests/              # backend

cd frontend
npm test                   # frontend (Vitest + Testing Library)
```
