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
indexer.py ──────────► embeddings (Ollama, qwen3-embedding:0.6b) → ChromaDB
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
  project_lock.py               # bloqueo por proyecto entre procesos (CLI, servidor, watcher)
  services.py                   # casos de uso compartidos por CLI, API y watcher
  engine/
    chunker.py                  # AST políglota (tree-sitter)
    git_watcher.py                # diff incremental + exclusión de código generado
    indexer.py                     # chunking + embeddings + ChromaDB
    doc_generator.py                # documentación .md incremental
    rag_engine.py                    # retrieval + grafo de llamadas + LLM
    scope_resolution.py               # desambiguación de nombres por scope
    text_sanitize.py                   # limpieza de respuestas del LLM
    chroma_utils.py                     # cliente ChromaDB + coherencia entre procesos (.index_version)
    llm.py                              # llamadas al LLM con num_ctx fijo y presupuesto de tokens
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

En JS/TS, las funciones asignadas a una variable (`const Boton = () => {...}`,
el estilo habitual de los componentes React) también se extraen como
funciones, y un `export` se incluye en el chunk junto con su JSDoc.

El código que no pertenece a ninguna función/clase (imports, constantes,
licencia) va a un chunk `module_level`, calculado **restando** los rangos ya
cubiertos por otros chunks: así el cuerpo de un `namespace { }` (C#/C++) o de
un `export` no se duplica ahí. Su rango de líneas es el real (de la primera a
la última pieza), aunque las piezas no sean contiguas.

## 4. Indexado (`indexer.py`)

- Usa `git_watcher.py` para calcular qué archivos cambiaron desde el
  último commit indexado (`ChangeSet`: añadidos/modificados/eliminados) —
  indexado **incremental**, no reprocesa todo el repo cada vez.
- Excluye código generado/vendor (`.pb.go`, `_pb2_grpc.py`, `node_modules/`,
  etc.) porque diluye la calidad de búsqueda.
- Si el commit guardado ya no existe (rebase, re-clonado) o se pide
  `beacon sync --full`, se vacía la colección antes de reindexar, para no dejar
  chunks de archivos que ya no existen. Un rename hacia una extensión no
  indexable se trata como borrado.
- Si Ollama no responde o falta el modelo, el sync falla **sin** marcar el
  commit como indexado (antes terminaba "ok" con 0 chunks).
- `beacon docs` exige que el índice esté al día con HEAD: la documentación se
  arma desde los chunks indexados, así que con el índice atrasado se
  documentaría código viejo.
- **Modelo de embeddings: `qwen3-embedding:0.6b`** (`engine/embeddings.py`).
  Se eligió midiendo con `tools/eval_retrieval.py` (30 preguntas en español
  sobre psf/requests) entre nomic-embed-text, bge-m3, embeddinggemma y
  qwen3-embedding: nomic, entrenado casi solo en inglés, dejaba la respuesta
  entre los 5 primeros en el 33% de las preguntas; qwen3-embedding, en el
  93%. Pesa ~0,6 GB y cabe junto a llama3:8b en una GPU de 8 GB. También se
  probó traducir la pregunta al inglés con llama3: con nomic ayudaba mucho,
  pero con un modelo multilingüe no mejora de forma significativa, así que
  se descartó para no agregar una llamada más al LLM.
- **Formato por modelo.** Cada modelo espera su propio formato de consulta y
  de documento (nomic: `"search_query: "`/`"search_document: "`, cuya
  omisión fue un bug real; qwen3: una instrucción de tarea en la consulta).
  `embeddings.profile_for` lo concentra, así que cambiar de modelo es cambiar
  una línea de `config.yaml`. Se usa `/api/embed` por lotes (un llamado por
  archivo) con `truncate=False`, para que un texto demasiado largo dé error
  y se divida en vez de recortarse en silencio. nomic-embed-text admite en
  realidad 2.048 tokens (no 8.192).
- **Cambio de modelo.** El modelo usado queda registrado en la colección de
  control. Si la configuración cambia, el próximo sync recrea la colección
  (la dimensión de los vectores cambia, ej. 768 → 1024) y reindexa todo; las
  consultas contra un índice de otro modelo responden con un error claro
  (409 en la API) en vez de resultados sin sentido.
- Los archivos vacíos (ej. `__init__.py`) no generan fragmentos.
- ChromaDB se crea con métrica coseno explícita
  (`metadata={"hnsw:space": "cosine"}`) — el default (L2) no es ideal para
  embeddings de texto.
- Chunks gigantes (>16000 caracteres) se dividen recursivamente a la mitad
  (respetando saltos de línea) hasta que el modelo los acepta, en vez de
  usar un umbral fijo adivinado.
- **El índice corresponde exactamente al commit registrado.** El sync fija el
  commit objetivo al empezar (`target`), calcula el diff contra él y lee el
  contenido de cada archivo **desde ese commit** (`GitWatcher.read_file_at`),
  no desde la carpeta. Antes se leía el disco y se registraba HEAD al final:
  un cambio sin commitear quedaba indexado como parte del commit, y un commit
  que llegaba durante el sync se marcaba como procesado sin haberlo sido. El
  modo `--uncommitted` sigue leyendo el disco a propósito y no registra commit.
- **Coherencia entre procesos.** Cada escritura del índice actualiza
  `data/<id>/.index_version`. ChromaDB embebido mantiene el índice vectorial en
  memoria por proceso, así que un servidor ya levantado no veía lo que
  indexaba otra consola (se reprodujo: el conteo cambiaba, la búsqueda no).
  `chroma_utils.get_chroma_client` descarta la instancia en memoria de ese
  proyecto cuando el marcador cambió en otro proceso, y la API recrea el
  `RAGEngine` cuando cambia la versión (`_get_engine`).
- **Reconstrucción limpia del índice.** hnswlib (el índice vectorial de
  ChromaDB) no elimina los vectores borrados, solo los marca. Antes, un
  `sync --full` borraba fragmento por fragmento: tras cinco reconstrucciones
  de microservices-demo el índice ocupaba 13 MB para 363 fragmentos (4 MB
  recién creado) y una consulta falló con "Cannot return the results in a
  contigious 2D array". Ahora una reconstrucción completa recrea la
  colección. Además, la consulta reintenta con menos candidatos si hnswlib
  no logra reunirlos (`RAGEngine._query`), y cada sync borra las carpetas de
  índices que ya no pertenecen a ninguna colección
  (`chroma_utils.remove_orphan_segments`): en Windows, ChromaDB no puede
  borrarlas al eliminar la colección porque el proceso aún las tiene
  abiertas.

## 5. Motor RAG (`rag_engine.py`)

1. **Retrieval semántico**: embebe la pregunta y busca los chunks más
   cercanos en ChromaDB. **Los tests se penalizan** (+0,08 de distancia)
   salvo que la pregunta sea sobre pruebas: sus nombres repiten las palabras
   de la pregunta (`test_http_303_changes_post_to_get`) y le ganaban a la
   función que la responde. Medido: Hit@5 de 80% a 93% en psf/requests.
   **Texto embebido con la ruta** (`indexer.embedding_text`): cada
   fragmento se embebe precedido de la ruta de su archivo; lo que se guarda
   y se entrega al modelo sigue siendo solo el código. En microservices-demo
   (Go, C#, JavaScript, Python y Java; conjunto `eval/microservices-demo.yaml`)
   el Hit@5 subió de 77% a 95% y el MRR de 0,57 a 0,63: el código solo no
   dice a qué servicio pertenece, y el generador de carga, que repite los
   nombres de las operaciones, le ganaba a los servicios. En psf/requests,
   Hit@1 de 70% a 73% y MRR de 0,80 a 0,83. Las firmas `@overload` de Python
   (solo tipos, sin cuerpo) ya no se indexan: ocupaban lugares del contexto
   con el mismo nombre que la implementación. Ambos cambios son el formato 2
   del índice (`INDEX_FORMAT`); un índice de formato anterior se reconstruye
   solo en el próximo sync.
   **Búsqueda híbrida** (`_identifier_matches`): las palabras de la pregunta
   con forma de identificador (snake_case, camelCase, CONSTANTE) se buscan
   también por coincidencia exacta, en el nombre del fragmento (descuento de
   0,10 en la distancia) y en su código (0,04; se ignora un identificador
   presente en más de 15 fragmentos, por demasiado común). Si la pregunta es
   de uso ("¿dónde se usa…?", "¿quién llama a…?") los descuentos se
   invierten. La distancia de esos fragmentos se calcula aparte con sus
   vectores, porque las consultas filtradas de hnswlib fallan cuando hay
   menos coincidencias que resultados pedidos. Medido con
   `eval/requests-identificadores.yaml`: Hit@5 de 80% a 93%, MRR de 0,70 a
   0,79 y fragmento correcto en el contexto de 87% a 100%; los conjuntos sin
   identificadores no cambiaron.
   **Contexto adaptativo** (`select_context`): además de los `top_k`, se
   suman hasta 5 fragmentos casi empatados con el último (margen 0,03), para
   preguntas que tocan varios archivos. Medido: el fragmento correcto llega
   al modelo en el 97% de las preguntas (vs. 93% con `top_k` fijo), con 8
   fragmentos en promedio; márgenes menores no agregaban nada y el
   presupuesto de tokens sigue siendo el tope final.
2. **Expansión por grafo de llamadas**: por cada chunk función/método
   recuperado, extrae candidatos de llamada por regex
   (`CALL_CANDIDATE_PATTERN`, filtrando palabras reservadas de varios
   lenguajes) y busca en ChromaDB un chunk cuyo `name` coincida
   exactamente. Si hay varios candidatos con el mismo nombre, desambigua
   comparando el prefijo de 2 niveles de carpeta (`scope_resolution.py`)
   entre el llamador y los candidatos; si sigue ambiguo, no se resuelve.
3. **Prompt anti-alucinación**: reglas explícitas — si el código citado
   llama a algo cuyo cuerpo no está en el contexto, el modelo debe decirlo
   en vez de inventar qué hace. Las reglas se ajustaron con mediciones
   (`tools/eval_answers.py`, `eval/answers-requests.yaml`): el modelo citaba
   "el fragmento 3" en vez de la ruta del archivo, a veces cerraba una
   respuesta correcta con la frase de "no encontré información" y aceptaba
   preguntas con premisas falsas. Ahora cada fragmento llega rotulado con su
   ruta y líneas, sin número (`as_context_block`), y hay una regla para las
   premisas falsas. Resultado en psf/requests: contenido de 75% a 100%,
   respuestas que citan el archivo de 33% a 83-92%, rechazos indebidos de 8%
   a 0% y 100% de rechazos correctos, sin código genérico agregado. En el
   conjunto de control (`eval/answers-microservices-demo.yaml`, escrito
   después y no usado para ajustar) los valores son menores: contenido 70%,
   cita 60%.
   La generación usa temperatura 0,2 y semilla fija (`LLM_TEMPERATURE`,
   `LLM_SEED`): con la temperatura por defecto (0,8) el resultado de la
   misma evaluación variaba entre corridas y no se podían comparar cambios.
4. **Ventana de contexto** (`engine/llm.py`): cada llamada fija
   `num_ctx=8192` (el máximo de llama3:8b). Sin eso Ollama cargaba el modelo
   con 4.096 tokens y, cuando el prompt no cabía, **descartaba el comienzo**
   —las reglas del sistema— sin avisar: medido con psf/requests, una consulta
   con `top_k=10` enviaba 6.525 tokens y el modelo procesaba 2.060. Además,
   antes de enviar se estima el tamaño y se dejan solo los fragmentos que
   caben (por prioridad), reservando 1.024 tokens para la respuesta; las
   fuentes devueltas son exactamente las que vio el modelo. Después de cada
   llamada se compara `prompt_eval_count` con lo enviado y se registra una
   advertencia si no coincide.
5. **Idioma**: la instrucción de idioma va al **principio** del system
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

- **Archivos grandes completos.** Antes el prompt se cortaba en 20.000
  caracteres sin avisar (con `sessions.py` de requests se documentaba el
  28% del archivo). Ahora, si el archivo no cabe en una llamada, se analiza
  por partes (notas por parte, condensadas si hace falta) y se redacta el
  documento final a partir de esas notas; el encabezado indica en cuántas
  partes se analizó.
- **Índice de componentes exacto.** Cada documento termina con un
  «Índice de componentes» generado desde el análisis sintáctico (nombre,
  tipo y líneas), no por el modelo, así que es completo aunque el modelo
  describa solo los principales.
- **Idioma.** Los prompts exigen español neutro; sin eso llama3 respondía
  en inglés en archivos con código y comentarios en inglés.

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

Un punto de diseño importante: el indexador lee el repositorio (su
historial git) en la máquina donde corre el **proceso backend**
(`beacon serve`), no en la del navegador del usuario. Esto significa que el selector de "ruta al repo" en la UI no
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
  `config/credentials.yaml`, **separado** de `config.yaml` (que la API
  expone en `GET /config`) para no filtrar secretos. Ninguno de los dos se
  versiona: en git solo están los `*.example.yaml`.

### Watcher automático

Toggle opcional por proyecto (`auto_watch`). Un thread en background
(`_auto_watch_loop` en `api.py`) llama a `sync()` cada 5 minutos para los
proyectos marcados — reutiliza la misma lógica idempotente del sync manual
(no hace nada si no hay commits nuevos), así que no duplica código.

Cómo vive el hilo:

- Se crea y arranca **al importar `core.api`** (`threading.Thread(...,
  daemon=True)` al final del módulo), es decir, cuando `beacon serve` (o el
  contenedor `beacon`) carga la app. No hay cron ni servicio del sistema
  operativo: vive mientras vive el proceso del backend y muere con él.
- La CLI no importa `core.api`, así que `beacon sync`/`ask`/`docs` nunca
  levantan el watcher.
- El ciclo duerme **antes** de revisar: la primera revisión es 5 minutos
  después del arranque.
- Cada ciclo (`_auto_watch_tick`) relee `config.yaml`, de modo que activar o
  desactivar `auto_watch` se aplica sin reiniciar.
- Coordinación en dos niveles:
  - `_claim_sync` (en memoria) evita dos syncs del mismo proyecto dentro del
    proceso del servidor y permite responder 409 de inmediato en la API.
  - `core/project_lock.py` coordina **entre procesos**: un bloqueo del sistema
    operativo sobre `data/<id>/.sync.lock` (`msvcrt.locking` en Windows,
    `fcntl.flock` en Linux/macOS), no bloqueante. Lo toman los servicios
    `sync_project` (API, watcher y `beacon sync`), `generate_docs` y el
    borrado con `--purge-data`. El sistema operativo lo
    libera si el proceso muere, así que no quedan locks huérfanos. `beacon
    export` excluye el archivo de lock del `.zip`.
  - El `git pull` de los proyectos clonados por URL ocurre **dentro** del
    lock (`services.sync_project`), así que nunca cambia archivos a mitad
    del indexado de otra consola.
- `beacon serve` usa un solo proceso (`reload=False`, sin `workers`). Con
  varios workers habría un watcher por worker: el lock evita escrituras
  simultáneas, pero las revisiones serían redundantes.
- Una excepción en un proyecto se registra con `logger.exception`, libera el
  claim marcando el estado `error`, y el ciclo continúa con los demás.

### Capa de servicios (`services.py`)

Los flujos del negocio viven una sola vez en `core/services.py`:
`register_project`, `unregister_project`, `sync_project` (pull del remoto +
índice + documentación opcional, todo bajo el lock del proyecto),
`generate_docs` y `pull_from_remote`. La CLI y la API solo traducen
entradas y errores (mensajes de consola o códigos HTTP), y el watcher usa
el mismo `sync_project` que el botón "Sincronizar".

Antes cada punto de entrada tenía su propia versión y se habían separado:
la API aceptaba token para repos privados y la CLI no (ahora `beacon add
--private`); la API ignoraba `auto_watch` al registrar; la CLI purgaba
código generado y la API no; y solo el watcher hacía `git pull`, así que
`beacon sync` y el botón de la UI nunca traían los cambios de un repo
clonado por URL. Además, el `git pull` ahora ocurre dentro del lock del
proyecto (antes el watcher lo hacía fuera, con una ventana en la que podía
coincidir con un sync de otra consola), y un id ya registrado se rechaza
antes de clonar.

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
- **Path traversal en el fallback de la SPA**: uvicorn decodifica `%2e%2e`
  pero no normaliza los `..`, así que `GET /%2e%2e/%2e%2e/config/credentials.yaml`
  devolvía el archivo (sin auth, porque las rutas de la SPA no la exigen).
  Ahora solo se sirven archivos que resuelvan dentro de `frontend/dist`.
- **Id de proyecto como nombre de carpeta**: el id se valida
  (`^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`) al crear/importar un proyecto; sin
  esto, `id=".."` más un purge borraba la raíz del repositorio. El purge
  además verifica que la carpeta resuelta quede dentro de `data/`.

**Autenticación**: opcional, vía la variable de entorno `BEACON_API_KEY`. Si
está seteada, un middleware (`api_key_middleware` en `api.py`) exige el
header `X-API-Key` en los prefijos `/projects`, `/config` y `/system` —
deliberadamente NO en `/healthz` (para que el healthcheck de Docker no
necesite conocer la clave) ni en las rutas de la SPA/estáticos (para que la
UI cargue siempre; la clave se ingresa después desde Configuración y queda
en `localStorage` del navegador). Sin la variable seteada (default), la API
sigue abierta — sigue siendo responsabilidad de quien despliega Beacon no
exponerlo así más allá de `localhost`/red interna sin un proxy de auth real
delante (ver README).

**Auditoría de dependencias** (`pip-audit`): se encontraron 37 CVEs
conocidos entre `gitpython`, `starlette`, `setuptools`, `click` y
`python-dotenv`. Corregido lo seguro de arreglar: `gitpython` subido a
`3.1.53` (de ~19 CVEs a 0 — era la dependencia más expuesta dado que
maneja clonado/pull de URLs potencialmente no confiables), `fastapi`
subido a `0.115.14`, y `python-dotenv` eliminado por completo (no se
usaba en ningún lado — resabio de la v1 pre-`config.yaml`). **Pendiente,
deliberado**: `starlette` (dependencia de `fastapi`) sigue en `0.41.3`
con CVEs conocidos — todos los fixes disponibles requieren
`starlette>=0.47`, pero `fastapi==0.115.x` exige `<0.47`. Arreglarlo de
fondo implica subir `fastapi` a una serie mayor (0.116+), lo que puede
cambiar comportamiento de la API y merece su propia ronda de testing
completa — no se hizo apurado al final del proyecto. Anotado como
trabajo futuro concreto (no un "no se sabía").

Mismo criterio del lado del frontend (`npm audit`): `react-router-dom`
tiene 2 CVEs moderados (open redirect, inyección en `deserializeErrors()`
SSR) cuyo fix solo existe en la serie mayor 7.x — la instalada es 6.30.4.
React Router v7 cambia parte de la API de ruteo; no se subió sin poder
retestear toda la navegación de la SPA. Trabajo futuro documentado, no
crítico dado que Beacon no usa SSR y el riesgo de open-redirect es bajo
en una app de un solo origen sin links a URLs externas generadas por
usuario.

## 10. Portabilidad y confiabilidad de despliegue

- **`beacon export`/`beacon import`** (`cli.py`): empaquetan
  `data/<project_id>/` (índice ChromaDB + docs) en un `.zip` con un
  `manifest.json` (metadata del proyecto, sin secretos). Pensado para poder
  llevar un proyecto ya indexado a otra máquina — típicamente para una
  demo o la defensa — sin depender de reindexar en vivo. El import valida
  que ningún miembro del zip pueda escribir fuera de `data/<id>/` (defensa
  contra "zip slip": rutas `../` en los nombres de archivo del zip).
- **Healthcheck real en Docker Compose**: el contenedor `ollama` expone
  `healthcheck: ollama list`, y `beacon` usa
  `depends_on: ollama: condition: service_healthy` — sin esto, `beacon`
  podía arrancar antes de que Ollama estuviera listo para responder,
  fallando la primera consulta. `GET /healthz` (sin auth, sin
  dependencias externas) es lo que usa el propio contenedor `beacon` para
  su `HEALTHCHECK` en el `Dockerfile`.

## 11. Testing

`pytest tests/` corre tests unitarios (chunker, config, doc_generator) y
de integración de la API (`fastapi.testclient.TestClient`) usando
monkeypatch para no mutar `config.yaml` real. Los flujos con LLM real
(generación de docs, respuestas del RAG) se verificaron manualmente contra
Ollama corriendo en local, no solo con mocks.
