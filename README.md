# Beacon

Plataforma **100% local (on-premise)** para detectar y mitigar deuda técnica en repositorios de código, usando RAG (retrieval-augmented generation) sobre modelos de lenguaje corriendo en tu propia máquina o servidor. Sin costo de API, sin que tu código salga de tu red.

Proyecto de título — INACAP, 2026.

## Qué hace

1. Indexa un repositorio de código con un **chunker políglota basado en AST** (tree-sitter), que entiende la estructura real de funciones, clases y métodos en 8+ lenguajes.
2. Genera embeddings locales (Ollama + `nomic-embed-text`) y los guarda en **ChromaDB**.
3. Responde preguntas sobre el código vía un motor RAG con **expansión por grafo de llamadas** (si el código citado llama a otra función indexada, se agrega automáticamente al contexto) y reglas explícitas anti-alucinación.
4. Genera documentación `.md` por archivo, incremental — solo regenera lo que cambió desde el último commit indexado.
5. Todo esto disponible por **CLI** y por una **interfaz web** (chat, navegador de documentación, configuración) servida por el mismo backend.

## Arquitectura

```
config/config.yaml       # proveedor de IA + registro de proyectos (multi-repo)
src/core/
  cli.py                  # comando `beacon`
  api.py                  # API REST (FastAPI) + sirve la UI compilada
  engine/
    chunker.py             # chunking AST políglota
    git_watcher.py          # detección incremental de cambios + exclusión de código generado
    indexer.py              # embeddings + ChromaDB
    doc_generator.py        # documentación .md incremental
    rag_engine.py            # retrieval + expansión por grafo de llamadas + LLM
frontend/                # UI (React + Vite + Tailwind)
tools/                   # scripts de diagnóstico
```

Cada proyecto indexado vive aislado en `data/<project_id>/` (su propio índice ChromaDB y su propia documentación generada) — varios repos conviven sin pisarse.

## Requisitos

- Python 3.10+
- [Node.js](https://nodejs.org/) 18+ (para compilar la UI)
- [Ollama](https://ollama.com/) corriendo localmente (o accesible por red)

## Instalación

```bash
# 1. Clonar y entrar al proyecto
git clone https://github.com/<tu-usuario>/beacon.git
cd beacon

# 2. Entorno virtual + dependencias del backend
python -m venv .venv
.venv/Scripts/activate   # Windows
# source .venv/bin/activate   # Linux/Mac
pip install -r requirements.txt
pip install -e .

# 3. Modelos de Ollama
ollama pull nomic-embed-text
ollama pull llama3:8b

# 4. Configuración
cp config/config.example.yaml config/config.yaml
# edita config/config.yaml: agrega el/los repo(s) que quieras indexar

# 5. (Opcional) Compilar la UI web
cd frontend
npm install
npm run build
cd ..
```

## Uso — CLI

```bash
beacon doctor                        # verifica Ollama, modelos, repos configurados
beacon projects                      # lista proyectos registrados
beacon sync <project_id>             # indexa/reindexa incrementalmente
beacon docs <project_id>             # genera documentación .md
beacon ask <project_id> "pregunta"   # consulta el RAG desde la terminal
beacon serve                         # levanta la API + UI en http://127.0.0.1:8000
```

## Uso — UI web

Con `beacon serve` corriendo (y la UI ya compilada, paso 5 de instalación), abre `http://127.0.0.1:8000`:

- **Chat**: preguntas en lenguaje natural sobre el código indexado, con fuentes citadas.
- **Docs**: navegador de la documentación generada por archivo.
- **Configuración**: proveedor/modelo de IA (auto-detectados desde Ollama), registro de nuevos proyectos (ruta local o clonado por URL desde GitHub/GitLab/Bitbucket), watcher automático opcional por proyecto.

Soporta español e inglés (selector en la barra lateral).

## Desarrollo del frontend

Para trabajar en la UI con recarga en caliente:

```bash
# Terminal 1
beacon serve

# Terminal 2
cd frontend
npm run dev   # http://localhost:5173, con proxy hacia la API en :8000
```

## Tests

```bash
pytest tests/
```

## Licencia

MIT — ver [LICENSE](LICENSE).
