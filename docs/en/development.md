[Español](../es/desarrollo.md) | **English**

# Development

## Code structure

```
config/config.yaml       # AI provider + project registry (multi-repo)
src/core/
  cli.py                  # `beacon` command: options and messages only
  api.py                  # REST API (FastAPI) + serves the compiled UI; maps domain errors to HTTP codes
  services.py             # use cases shared by the CLI, the API and the watcher (register, sync, docs)
  portability.py          # export and import of an indexed project (.zip)
  storage.py              # disk usage per project and selective cleanup
  config.py               # config.yaml and credentials.yaml (locked, atomic writes)
  project_lock.py         # cross-process locks
  study/                  # study mode (launcher.py prepares the session; api.py serves /study)
  engine/
    backends/              # model servers (Ollama and OpenAI-compatible) behind a common interface
    chunker.py             # polyglot AST chunking
    git_watcher.py          # incremental change detection + exclusion of generated code
    index_store.py          # index collections and their control record (commits, model, format)
    indexer.py              # embeddings + ChromaDB
    doc_generator.py        # incremental .md documentation
    overview.py             # repository overview built from the documentation
    retrieval.py            # hybrid search and ranking
    call_graph.py           # call-graph expansion
    prompting.py            # system rules, context and fit to the model window
    rag_engine.py           # coordinates retrieval, call graph, prompt and LLM
frontend/                # UI (React + Vite + Tailwind)
tools/                   # diagnostic scripts
```

## Model backends

The engine never talks to a model server directly: it uses a `Backend` (`src/core/engine/backends/`) that exposes `chat`, `embed`, `list_models` and `health`, and raises its own errors (`BackendUnavailableError`, `ModelNotFoundError`, `InputRejectedError`). There are two adapters: Ollama and a generic OpenAI-compatible one (LM Studio, llama.cpp, vLLM). Only servers on this machine or on a private network are accepted; a version with consent, spending limit and API key for commercial APIs lives in the branch `experimental/paid-apis`.

`CodebaseIndexer`, `DocGenerator` and `RAGEngine` receive their backend and ChromaDB client (and the indexer and generator their `GitWatcher`) as optional constructor arguments; by default they use the ones of the configuration and the project. The tests pass doubles that way.

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
