[English](../en/development.md) | **Español**

# Desarrollo

## Estructura del código

```
config/config.yaml       # proveedor de IA + registro de proyectos (multi-repo)
src/core/
  cli.py                  # comando `beacon`: solo opciones y mensajes
  api.py                  # API REST (FastAPI) + sirve la UI compilada; traduce los errores del dominio a códigos HTTP
  services.py             # casos de uso compartidos por la CLI, la API y el watcher (registrar, sincronizar, documentar)
  portability.py          # exportar e importar un proyecto indexado (.zip)
  storage.py              # espacio en disco por proyecto y limpieza selectiva
  config.py               # config.yaml y credentials.yaml (escrituras con bloqueo y atómicas)
  project_lock.py         # bloqueos entre procesos
  study/                  # modo estudio (launcher.py prepara la sesión; api.py sirve /study)
  engine/
    backends/              # servidores de modelos (Ollama y compatibles con OpenAI) tras una interfaz común
    chunker.py             # chunking AST políglota
    git_watcher.py          # detección incremental de cambios + exclusión de código generado
    index_store.py          # colecciones del índice y su registro de control (commits, modelo, formato)
    indexer.py              # embeddings + ChromaDB
    doc_generator.py        # documentación .md incremental
    overview.py             # resumen del repositorio armado desde la documentación
    retrieval.py            # búsqueda híbrida y ranking
    call_graph.py           # expansión por grafo de llamadas
    prompting.py            # reglas del sistema, contexto y ajuste a la ventana del modelo
    rag_engine.py           # coordina búsqueda, grafo de llamadas, prompt y LLM
frontend/                # UI (React + Vite + Tailwind)
tools/                   # scripts de diagnóstico
```

## Backends de modelos

El motor nunca habla directamente con un servidor de modelos: usa un `Backend` (`src/core/engine/backends/`) que expone `chat`, `embed`, `list_models` y `health`, y lanza sus propios errores (`BackendUnavailableError`, `ModelNotFoundError`, `InputRejectedError`). Hay dos adaptadores: Ollama y uno genérico compatible con OpenAI (LM Studio, llama.cpp, vLLM). Solo se aceptan servidores en esta máquina o en una red privada; una versión con consentimiento, límite de gasto y clave de API para APIs comerciales vive en la rama `experimental/paid-apis`.

`CodebaseIndexer`, `DocGenerator` y `RAGEngine` reciben su backend y su cliente de ChromaDB (y el indexador y el generador, su `GitWatcher`) como argumentos opcionales del constructor; por defecto usan los de la configuración y el proyecto. Las pruebas pasan así sus dobles.

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
