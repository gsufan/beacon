# Arquitectura de Beacon

Documento técnico de referencia del propio sistema Beacon (no confundir con
`data/<project_id>/docs/`, que es la documentación que Beacon *genera* sobre
otros repositorios).

## 1. Visión general del pipeline

```
repo de código
   │
   ▼
chunker.py ──────────► chunks (función/clase/método) vía AST (tree-sitter)
   │
   ▼
indexer.py ──────────► embeddings (Ollama, nomic-embed-text) → ChromaDB
   │
   ▼
rag_engine.py ───────► retrieval + expansión por grafo de llamadas + LLM
   │
   ▼
respuesta (CLI / API / UI web)

doc_generator.py ────► documentación .md por archivo (LLM, incremental)
```

Todo corre **local**: Ollama para embeddings/LLM, ChromaDB para el índice
vectorial, FastAPI para servir la API + la UI compilada. Cero llamadas a
servicios externos, cero costo de API.

## 2. Estructura de carpetas

```
config/config.yaml          # proveedor de IA + registro de proyectos
config/credentials.yaml     # tokens de repos privados (gitignoreado, NO va en config.yaml)
src/core/
  config.py                  # carga/escritura de config.yaml
  projects.py                 # ProjectContext, aislamiento por proyecto
  cli.py                       # comando `beacon`
  api.py                        # API REST + sirve la UI compilada
  engine/
    chunker.py                  # AST políglota (tree-sitter)
    git_watcher.py                # diff incremental + exclusión de código generado
    indexer.py                     # chunking + embeddings + ChromaDB
    doc_generator.py                # documentación .md incremental
    rag_engine.py                    # retrieval + grafo de llamadas + LLM
    scope_resolution.py               # desambiguación de nombres por scope
    text_sanitize.py                   # limpieza de respuestas del LLM
    chroma_utils.py                     # cliente ChromaDB centralizado
frontend/                    # UI (React + Vite + Tailwind)
tools/                       # scripts de diagnóstico (rank_check, diagnose_query, etc.)
data/<project_id>/           # índice + docs de CADA proyecto indexado, aislados entre sí
```

Cada proyecto registrado en `config.yaml` tiene su propia carpeta
`data/<project_id>/{chroma_db,docs}` — varios repos pueden convivir sin
pisarse (multi-tenant local).

## 3. El chunker (`chunker.py`)

Usa `tree-sitter` (fijo en `0.21.3` — la 0.23.x rompe la API de
`tree-sitter-languages`) para parsear el AST real de cada lenguaje, en vez
de dividir el archivo por líneas o por regex. Extrae funciones, clases y
métodos como unidades independientes, cada una con su propio `chunk_id` y
metadata (`file_path`, `language`, `chunk_type`, `name`, `start_line`,
`end_line`).

**Decisión clave**: el chunker adjunta el comentario/docstring
inmediatamente anterior (sin línea en blanco de por medio) al chunk que
documenta. Esto mejora mucho la recuperación semántica porque el embedding
incluye la intención declarada del código, no solo su implementación.

## 4. Indexado (`indexer.py`)

- Usa `git_watcher.py` para calcular qué archivos cambiaron desde el
  último commit indexado (`ChangeSet`: añadidos/modificados/eliminados) —
  indexado **incremental**, no reprocesa todo el repo cada vez.
- Excluye código generado/vendor (`.pb.go`, `_pb2_grpc.py`, `node_modules/`,
  etc.) porque diluye la calidad de búsqueda.
- **`nomic-embed-text` requiere los prefijos `"search_document: "` /
  `"search_query: "`** en cada texto antes de embeberlo — sin esto la
  recuperación semántica es notablemente peor (bug real detectado en una
  sesión anterior).
- ChromaDB se crea con métrica coseno explícita
  (`metadata={"hnsw:space": "cosine"}`) — el default (L2) no es ideal para
  embeddings de texto.
- Chunks gigantes (>16000 caracteres) se dividen recursivamente a la mitad
  (respetando saltos de línea) hasta que el modelo los acepta, en vez de
  usar un umbral fijo adivinado.

## 5. Motor RAG (`rag_engine.py`)

1. **Retrieval semántico**: embebe la pregunta y busca los `top_k` chunks
   más cercanos en ChromaDB.
2. **Expansión por grafo de llamadas**: por cada chunk función/método
   recuperado, extrae candidatos de llamada por regex
   (`CALL_CANDIDATE_PATTERN`, filtrando palabras reservadas de varios
   lenguajes) y busca en ChromaDB un chunk cuyo `name` coincida
   exactamente. Si hay varios candidatos con el mismo nombre, desambigua
   comparando el prefijo de 2 niveles de carpeta (`scope_resolution.py`)
   entre el llamador y los candidatos; si sigue ambiguo, no se resuelve.
3. **Prompt anti-alucinación**: reglas explícitas — si el código citado
   llama a algo cuyo cuerpo no está en el contexto, el modelo debe decirlo
   en vez de inventar qué hace.
4. **Idioma**: la instrucción de idioma va al **principio** del system
   prompt (no al final) y se refuerza con `text_sanitize.strip_preamble`,
   que descarta cualquier meta-comentario que el modelo agregue antes del
   contenido real (modelos chicos como `llama3:8b` a veces "confirman" la
   instrucción en vez de solo seguirla).

## 6. Documentación generada (`doc_generator.py`)

Genera un `.md` por archivo con secciones fijas (Propósito, Componentes
principales, Dependencias, Riesgos de deuda técnica), usando el mismo
patrón incremental que el indexador (solo regenera archivos que cambiaron).
El prompt exige evidencia concreta para cada riesgo citado (nombre de
función/variable, qué hace exactamente) — nada de "falta manejo de
errores en algunos métodos" genérico.

## 7. API (`api.py`) y frontend

FastAPI expone endpoints multi-proyecto (`/projects/{id}/query`,
`/projects/{id}/docs`, `/config`, `/system/*`, etc.) y sirve la SPA
compilada (`frontend/dist`) como estáticos, con fallback a `index.html`
para las rutas de React Router. La documentación automática de FastAPI se
movió a `/api/docs` (por defecto choca con la página `/docs` del frontend).

El frontend (React + Vite + Tailwind) tiene 3 vistas: Chat, Docs,
Configuración — con un sistema de traducción propio (`lib/i18n.ts`) que
cubre tanto los textos estáticos de la UI como el idioma en que responde
el LLM.

### Registro de proyectos: local vs. clonado por URL

Un punto de diseño importante: el indexador siempre lee archivos del disco
donde corre el **proceso backend** (`beacon serve`), nunca del navegador
del usuario. Esto significa que el selector de "ruta al repo" en la UI no
puede ser un `<input type="file">` del navegador (vería el filesystem
equivocado si Beacon corre en un servidor remoto). Por eso hay dos modos:

- **Ruta local**: explorador de carpetas *server-side*
  (`GET /system/browse-dirs`) — cubre el caso de correr Beacon en tu
  propia máquina.
- **Clonar desde URL**: el servidor clona el repo con `gitpython`
  (`git.Repo.clone_from`) a `data/<id>/repo/` — cubre el caso de un
  servidor central analizando un repo que vive en otro lado (GitHub,
  GitLab, Bitbucket — es protocolo git estándar, no hace falta SDK por
  proveedor). Los tokens de repos privados se guardan en
  `config/credentials.yaml`, **separado** de `config.yaml` (que sí está
  versionado en git) para no filtrar secretos.

### Watcher automático

Toggle opcional por proyecto (`auto_watch`). Un thread en background
(`_auto_watch_loop` en `api.py`) llama a `sync()` cada 5 minutos para los
proyectos marcados — reutiliza la misma lógica idempotente del sync manual
(no hace nada si no hay commits nuevos), así que no duplica código.

## 8. Decisiones descartadas (y por qué)

- **Grafo de llamadas visual** (nodos/aristas interactivos): se construyó
  en una iteración (`call_graph.py`) pero se descartó — con repos reales
  el diagrama se vuelve ilegible rápido y no aportaba más valor que la
  expansión por grafo ya integrada al RAG. Se removió junto con el panel
  de "Arquitectura" y el pipeline de riesgos de deuda técnica estructurados
  (JSON) que solo alimentaba esa vista.
- **Ejecutable nativo por SO** (PyInstaller para Windows/Mac/Linux): se
  evaluó como parte de la Fase D y se descartó por ahora. Ollama es una
  dependencia externa pesada (varios GB con modelos) que no tiene sentido
  embeber en un instalador liviano — el usuario lo instala aparte de
  todos modos, así que un ejecutable standalone del backend no resuelve
  el problema real de "instalación fácil". Docker Compose sí resuelve eso
  con menos esfuerzo: `docker compose up` levanta Beacon y Ollama juntos,
  en cualquier SO con Docker instalado (ver `Dockerfile` y
  `docker-compose.yml` en la raíz).

## 9. Seguridad

Auditoría aplicada contra OWASP Top 10 y, como marco de referencia legal,
la Ley 21.719 de Chile (moderniza la protección de datos personales,
vigente desde diciembre de 2026). Beacon indexa código fuente y metadata
de archivos, no datos de autores de commits (`git_watcher.py` solo trabaja
con paths, nunca con `commit.author`/`committer`) — la ley aplica de forma
marginal dado el alcance actual, pero sus principios (minimización, no
exponer secretos) guiaron las siguientes decisiones:

- **CORS** restringido a los orígenes reales de desarrollo (`vite.config.ts`
  dev server), no `allow_origins=["*"]` — sin esto, cualquier página que el
  usuario visitara podría hacer requests al backend si estaba en la misma
  red.
- **`/system/browse-dirs` acotado a `BROWSE_ROOT`** (home del usuario del
  proceso): sin esta restricción, se podía enumerar cualquier carpeta del
  disco (`?path=C:\` o `?path=/etc`) — reconocimiento total del sistema vía
  un endpoint pensado solo para elegir la ruta de un repo.
- **Validación de esquema en `repo_url`** antes de pasarlo a
  `git.Repo.clone_from`: solo se aceptan `http(s)://`, `ssh://` o `git@...`.
  Git soporta un esquema `ext::` que invoca un comando de transporte
  arbitrario — un vector de inyección de comandos conocido si se deja pasar
  una URL sin validar.
- **El token de repos privados no se persiste en `.git/config`**: se
  reinyecta en la URL solo para el `clone`/`pull` puntual (tanto al
  registrar el proyecto como en cada ciclo del watcher automático) y se
  restaura la URL sin token inmediatamente después — reduce a un solo lugar
  (`config/credentials.yaml`, gitignoreado) dónde vive el secreto en disco.
- **Path traversal en `GET /projects/{id}/docs`**: `file_path` se resuelve
  y se valida que el resultado siga dentro de `docs_dir` del proyecto antes
  de leer, en vez de concatenar la ruta directo.

**Limitación conocida, deliberada dado el alcance del proyecto**: la API no
tiene autenticación — cualquiera con acceso de red al puerto puede leer y
modificar configuración. Aceptable para una herramienta de uso local/interno
de un dev o equipo, pero **no** debe exponerse a internet sin un proxy de
autenticación delante (ver README). Agregar auth real (API key o similar)
queda documentado como trabajo futuro, no crítico para el alcance actual.

## 10. Testing

`pytest tests/` corre tests unitarios (chunker, config, doc_generator) y
de integración de la API (`fastapi.testclient.TestClient`) usando
monkeypatch para no mutar `config.yaml` real. Los flujos con LLM real
(generación de docs, respuestas del RAG) se verificaron manualmente contra
Ollama corriendo en local, no solo con mocks.
