**English** | [Español](README.es.md)

# Beacon

[![Tests](https://github.com/gsufan/beacon/actions/workflows/tests.yml/badge.svg)](https://github.com/gsufan/beacon/actions/workflows/tests.yml)

A **self-hosted (on-premise)** service that helps you understand and document the technical debt of a code repository using RAG (retrieval-augmented generation) on language models that run on your own machine or server: no cost per query and no code sent to external services. Answers cite the file and lines they come from, so they can be verified.

Degree project — INACAP, 2026.

## What it does

1. Indexes a code repository with a **polyglot, AST-based chunker** (tree-sitter) that understands the real structure of functions, classes and methods in 8+ languages.
2. Generates local embeddings (Ollama + `qwen3-embedding:0.6b`, multilingual) and stores them in **ChromaDB**.
3. Answers questions through a RAG engine with **call-graph expansion** (if the cited code calls another indexed function, it is added to the context automatically) and explicit anti-hallucination rules.
4. Generates one `.md` documentation file per source file, incrementally: it only regenerates what changed since the last indexed commit.
5. All of this is available through a **CLI** and a **web interface** (chat, documentation browser, settings) served by the same backend.

## Ways to use it

- **One person:** local installation from the repository and the console (`beacon ask`, `beacon sync`). See the [manual installation guide](docs/en/manual-installation.md).
- **A team:** one server running Docker Compose with a single shared index, accessed from the browser, the console or the REST API. This is the recommended way and is described below.

## Quick start with Docker (recommended)

Starts Beacon **and** Ollama in containers, with no need to install Python or Node on your machine.

```bash
git clone https://github.com/gsufan/beacon.git
cd beacon
cp config/config.example.yaml config/config.yaml
```

Edit `config/config.yaml`: keep `ollama_host: http://ollama:11434` (the name `ollama` is resolved by the internal Docker Compose network — do not use `localhost` there) and add the repository or repositories you want to index.

```bash
docker compose up -d --build

# first time only: download the models inside the Ollama container
docker compose exec ollama ollama pull qwen3-embedding:0.6b
docker compose exec ollama ollama pull llama3:8b

# index a project
docker compose exec beacon beacon sync <project_id>
docker compose exec beacon beacon docs <project_id>
```

The UI is served at `http://localhost:8000`. `./config` and `./data` are
mounted from your machine, so settings and indexes persist across container
restarts.

More details (GPU on Windows, local paths, healthcheck) in [docs/en/docker.md](docs/en/docker.md).

## Take part in the study (study mode)

If you were invited to try Beacon for the evaluation study, you need Python 3.10, 3.11 or 3.12 (3.13 or later does not work yet), Git, Node 20+ and [Ollama](https://ollama.com) with the models installed.

```bash
git clone https://github.com/gsufan/beacon.git
cd beacon
python -m venv .venv
# Windows: .venv\Scripts\activate     Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
ollama pull qwen3-embedding:0.6b
ollama pull llama3:8b
cd frontend && npm install && npm run build && cd ..
beacon study
```

The assistant opens in the browser and guides you: you accept participation, choose whether or not to share your machine's details, paste the address of a public repository, ask at least 3 questions rating each answer, and review the result. When you finish, `beacon-estudio-resultados-<id>.json` is left in the project folder (without code, paths or your name); send it through the channel given in the invitation. Details in [docs/en/study-mode.md](docs/en/study-mode.md).

## Documentation

| Document | Contents |
|---|---|
| [Manual installation](docs/en/manual-installation.md) | Without Docker, on Windows and Linux/macOS; how to share it with a team |
| [Usage](docs/en/usage.md) | CLI, web interface, automatic sync, exporting and importing projects |
| [Docker](docs/en/docker.md) | Details and special cases of the container deployment |
| [Measured quality](docs/en/measured-quality.md) | Results for search, answers and load tests |
| [Security](docs/en/security.md) | Known limitations and the API key |
| [Study mode](docs/en/study-mode.md) | Taking part in the study and analysing the results received |
| [Development](docs/en/development.md) | Code structure, frontend and tests |

The documentation is also available in Spanish under [docs/es/](docs/es/).

## Results at a glance

On `psf/requests` (30 questions), the fragment that answers the question appears among the top five results in 93 % of cases. With 2,000 files, a full index takes 6 minutes and search has a p95 of 35 ms (not counting the language model). The question sets are small: they are useful for comparing configurations, not as a general guarantee. Details in [Measured quality](docs/en/measured-quality.md).

## License

MIT — see [LICENSE](LICENSE).
