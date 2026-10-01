# Beacon

[![Tests](https://github.com/gsufan/beacon/actions/workflows/tests.yml/badge.svg)](https://github.com/gsufan/beacon/actions/workflows/tests.yml)

Plataforma **local (on-premise)** de apoyo para comprender y documentar la deuda técnica de un repositorio de código, usando RAG (retrieval-augmented generation) sobre modelos de lenguaje que se ejecutan en la propia máquina o servidor de la organización: sin costo por consulta y sin enviar el código a servicios externos. Las respuestas citan el archivo y las líneas de donde salen, para que puedan verificarse.

Proyecto de título — INACAP, 2026.

## Qué hace

1. Indexa un repositorio de código con un **chunker políglota basado en AST** (tree-sitter), que entiende la estructura real de funciones, clases y métodos en 8+ lenguajes.
2. Genera embeddings locales (Ollama + `qwen3-embedding:0.6b`, multilingüe) y los guarda en **ChromaDB**.
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

## Instalación con Docker (recomendada)

Levanta Beacon **y** Ollama en contenedores, sin instalar Python ni Node en tu máquina.

```bash
git clone https://github.com/gsufan/beacon.git
cd beacon
cp config/config.example.yaml config/config.yaml
```

Edita `config/config.yaml`: deja `ollama_host: http://ollama:11434` (el nombre `ollama` lo resuelve la red interna de Docker Compose — no uses `localhost` ahí) y agrega el/los repo(s) que quieras indexar.

```bash
docker compose up -d --build

# primera vez: descargar los modelos dentro del contenedor de Ollama
docker compose exec ollama ollama pull qwen3-embedding:0.6b
docker compose exec ollama ollama pull llama3:8b

# indexar un proyecto
docker compose exec beacon beacon sync <project_id>
docker compose exec beacon beacon docs <project_id>
```

La UI queda en `http://localhost:8000`. `./config` y `./data` quedan
montados desde tu máquina, así que la configuración y los índices
persisten entre reinicios del contenedor.

**Nota sobre `repo_path` local**: si registras un proyecto con `source_type: local` apuntando a una ruta de tu máquina, esa ruta tiene que estar además montada como volumen en `docker-compose.yml` para que el contenedor la vea — el modo **"Clonar desde URL"** (ver Configuración en la UI) no tiene este problema, porque el clonado ocurre dentro del contenedor.

Sin GPU, Ollama corre sobre CPU (más lento pero funciona). El compose incluye, comentada, la configuración para usar GPU NVIDIA si el host la tiene.

El contenedor `beacon` espera a que `ollama` pase su healthcheck (`depends_on: condition: service_healthy`) antes de arrancar, así que no falla la primera consulta por arrancar antes de que Ollama esté listo.

## Instalación manual (sin Docker)

Todo se hace desde una consola; no hace falta Docker.

### Requisitos

- **Python 3.10, 3.11 o 3.12** (recomendado 3.11, que es la versión que usan
  Docker y la CI). Python 3.13 o superior **no funciona todavía**: la
  dependencia `tree-sitter-languages` no publica paquetes para esas versiones y
  `pip install` falla con *"No matching distribution found"*. En Windows puedes
  tener varias versiones instaladas y elegir con `py -3.11`.
- [Node.js](https://nodejs.org/) 20+ (para compilar la UI web y correr sus tests)
- [Ollama](https://ollama.com/) instalado y corriendo (la app de escritorio lo
  deja corriendo en segundo plano; si no, `ollama serve` en otra consola)
- Git

### Pasos (Windows, PowerShell)

```powershell
# 1. Clonar y entrar al proyecto
git clone https://github.com/gsufan/beacon.git
cd beacon

# 2. Entorno virtual con Python 3.11 + dependencias del backend
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -e .

# 3. Modelos de Ollama (una sola vez, ~5,3 GB en total)
ollama pull qwen3-embedding:0.6b
ollama pull llama3:8b

# 4. Configuración base
Copy-Item config\config.example.yaml config\config.yaml
# (Opcional) tokens para repos privados clonados por URL
Copy-Item config\credentials.example.yaml config\credentials.yaml

# 5. Comprobar que todo está bien
beacon doctor

# 6. Registrar e indexar un repositorio
beacon add mi-proyecto --repo-path C:\ruta\al\repo    # o: --url https://github.com/usuario/repo.git
beacon sync mi-proyecto
beacon ask mi-proyecto "¿Qué hace este proyecto?"

# 7. (Opcional) Compilar y levantar la UI web
cd frontend
npm install
npm run build
cd ..
beacon serve        # http://127.0.0.1:8000 — dejar esta consola abierta
```

Notas para Windows:

- Si `Activate.ps1` da un error de *"la ejecución de scripts está
  deshabilitada"*, usa `cmd.exe` y activa con `.venv\Scripts\activate.bat`, o
  no actives el entorno y llama directamente a `.venv\Scripts\beacon.exe`.
- En `cmd.exe` se copia con `copy` en vez de `Copy-Item`.
- Ejecuta la CLI desde PowerShell, cmd o Windows Terminal, no desde Git Bash
  (ver la nota sobre terminales más abajo).

### Pasos (Linux / macOS)

```bash
git clone https://github.com/gsufan/beacon.git && cd beacon
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
ollama pull qwen3-embedding:0.6b && ollama pull llama3:8b
cp config/config.example.yaml config/config.yaml
beacon doctor
beacon add mi-proyecto --repo-path ../mi-proyecto
beacon sync mi-proyecto
cd frontend && npm install && npm run build && cd ..   # opcional: UI web
beacon serve
```

Si levantas `beacon serve` sin haber compilado la UI, la API funciona igual y
la raíz (`http://127.0.0.1:8000`) muestra cómo compilarla.

## Uso — CLI

```bash
beacon doctor                        # verifica Ollama, modelos, repos configurados
beacon projects                      # lista proyectos registrados

# --- gestión de proyectos (CRUD) ---
beacon add <id> --repo-path <ruta>   # registra un repo local ya clonado
beacon add <id> --url <repo_url>     # o clona uno remoto y lo registra
beacon add <id> --url <repo_url> --private   # repo privado: pide un token (no se muestra al escribirlo)
beacon edit <id> --name "..."        # edita nombre y/o repo_path de un proyecto ya registrado
beacon edit <id> --repo-path <ruta>
beacon edit <id> --auto-watch        # activa el watcher automático (--no-auto-watch lo desactiva)
beacon remove <id>                   # desregistra un proyecto (config.yaml)
beacon remove <id> --purge-data      # además borra data/<id>/ (índice + docs) del disco

# --- indexado y consultas ---
beacon sync <project_id>             # indexa/reindexa incrementalmente (en repos clonados por URL, antes hace git pull)
beacon sync <project_id> --docs      # además actualiza la documentación (igual que el botón "Sincronizar" de la UI)
beacon sync <project_id> --full      # rehace el índice completo (ej. tras actualizar Beacon)
beacon docs <project_id>             # genera documentación .md (requiere 'sync' al día)
beacon docs <project_id> --full      # regenera toda la documentación
beacon ask <project_id> "pregunta"   # consulta el RAG desde la terminal
beacon export <project_id>           # empaqueta el índice+docs en un .zip portable
beacon import <archivo.zip>          # registra un proyecto desde un .zip exportado
beacon serve                         # levanta la API + UI en http://127.0.0.1:8000
```

> **Nota sobre terminales en Windows**: los comandos de la CLI usan `rich` para
> la salida con colores/checkmarks, lo que requiere una consola Win32 real.
> En **Git Bash / MinTTY** eso puede hacer que comandos como `beacon doctor`
> fallen con un traceback al intentar escribir un carácter con color (la
> terminal no expone el handle de consola que `rich` necesita). No es un bug
> de Beacon — ejecuta la CLI desde **PowerShell**, **cmd.exe** o **Windows
> Terminal**, donde funciona sin problemas.

## Uso — UI web

Con `beacon serve` corriendo (y la UI ya compilada, paso 5 de instalación), abre `http://127.0.0.1:8000`:

- **Chat**: preguntas en lenguaje natural sobre el código indexado, con fuentes citadas.
- **Docs**: navegador de la documentación generada por archivo.
- **Configuración**: proveedor/modelo de IA (auto-detectados desde Ollama), registro de nuevos proyectos (ruta local o clonado por URL desde GitHub/GitLab/Bitbucket), watcher automático opcional por proyecto.

Soporta español e inglés (selector en la barra lateral).

## Watcher automático: cuándo corre

El watcher no es un proceso aparte ni un cron job: es un **hilo en segundo
plano dentro del proceso de `beacon serve`** (`_auto_watch_loop` en
`src/core/api.py`). Cada 5 minutos revisa los proyectos con `auto_watch`
activado; si el proyecto es remoto hace `git pull`, y luego sincroniza solo si
hay commits nuevos. Funciona igual con o sin Docker: lo único que necesita es
que el servidor esté levantado.

Casos límite a tener en cuenta:

- **Sin `beacon serve` no hay watcher.** Los comandos de la CLI (`beacon sync`,
  `beacon ask`, etc.) no levantan el hilo; si solo usas la CLI, sincroniza a
  mano con `beacon sync <id>`.
- **Se apaga con el servidor.** El hilo es *daemon*: al cerrar `beacon serve`
  (o detener el contenedor `beacon`) el watcher se detiene con él y retoma al
  volver a levantarlo.
- **La primera revisión ocurre 5 minutos después de arrancar**, no al inicio
  (el ciclo duerme antes de revisar). Si necesitas el índice al día de
  inmediato, usa `beacon sync <id>` o el botón "Sincronizar" de la UI.
- **Los cambios en `auto_watch` no requieren reiniciar.** Cada ciclo relee
  `config.yaml`, así que `beacon edit <id> --auto-watch` / `--no-auto-watch`
  (o el toggle de la UI) se aplica en el siguiente ciclo.
- **No se pisa con otros syncs, ni siquiera desde otra consola.** Cada
  proyecto tiene un bloqueo a nivel de sistema operativo
  (`data/<id>/.sync.lock`). Si el watcher, la UI o un `beacon sync`/`beacon
  docs` en otra consola ya está trabajando sobre un proyecto, el segundo no
  empieza: la CLI termina con un aviso, la UI muestra el mensaje en el estado
  del sync y el watcher reintenta en el siguiente ciclo. Si un proceso se cae
  a mitad (Ctrl+C, se cierra la consola), el sistema operativo libera el
  bloqueo solo; no hay que borrar nada a mano.
- **Un error no detiene el watcher.** Si falla un proyecto (por ejemplo, un
  `git pull` sin red), se registra en el log, el proyecto queda con estado
  `error` en la UI y el ciclo sigue con los demás.
- **Un solo proceso de servidor.** `beacon serve` levanta Uvicorn con un único
  proceso, así que hay un único watcher. Con varios *workers* (`uvicorn
  --workers N`) habría un watcher por worker; el bloqueo por proyecto evita que
  escriban a la vez, pero se harían revisiones redundantes, así que esa
  configuración no se recomienda.

## Desarrollo del frontend

Para trabajar en la UI con recarga en caliente:

```bash
# Terminal 1
beacon serve

# Terminal 2
cd frontend
npm run dev   # http://localhost:5173, con proxy hacia la API en :8000
```

## Calidad de la búsqueda (medida)

`tools/eval_retrieval.py` mide la recuperación contra conjuntos de
preguntas de referencia, escritas en español y cada una con la función que
la responde:

- `eval/requests.yaml`: 30 preguntas sobre psf/requests (Python).
- `eval/microservices-demo.yaml`: 22 preguntas sobre
  GoogleCloudPlatform/microservices-demo, 11 servicios en Go, C#,
  JavaScript, Python y Java.
- `eval/requests-identificadores.yaml` y
  `eval/microservices-demo-identificadores.yaml`: 15 y 10 preguntas que
  nombran un identificador ("¿para qué sirve `super_len`?", "¿dónde se usa
  `max_redirects`?"), como las hace quien ya vio el nombre en un error o un
  log.

```bash
python tools/eval_retrieval.py <project_id> eval/requests.yaml
```

Si una función esperada no existe en el índice, la herramienta se detiene
antes de medir, para que un error en el conjunto no se cuente como fallo de
la búsqueda.

| Configuración | Hit@1 | Hit@5 | Hit@10 | MRR@10 |
|---|---|---|---|---|
| nomic-embed-text (versión anterior) | 7% | 33% | 40% | 0,19 |
| qwen3-embedding:0.6b + tests penalizados | 70% | 93% | 100% | 0,80 |
| + ruta del archivo en el texto embebido, sin firmas `@overload` (actual) | 73% | 93% | 100% | 0,83 |

Con el contexto adaptativo, el fragmento que responde la pregunta llega al
modelo en el 97% de los casos (8 fragmentos en promedio).

Resultados por repositorio con la configuración actual:

| Repositorio | Preguntas | Hit@1 | Hit@5 | Hit@10 | MRR@10 | En contexto |
|---|---|---|---|---|---|---|
| psf/requests (Python) | 30 | 73% | 93% | 100% | 0,83 | 97% |
| microservices-demo (5 lenguajes) | 22 | 41% | 95% | 95% | 0,63 | 95% |
| psf/requests, con identificadores | 15 | 67% | 93% | 100% | 0,79 | 100% |
| microservices-demo, con identificadores | 10 | 100% | 100% | 100% | 1,00 | 100% |

En el repositorio políglota, el mayor avance vino de embeber cada fragmento
junto con la ruta de su archivo (Hit@5 de 77% a 95%): el código solo no dice
a qué servicio pertenece, y el generador de carga (`locustfile.py`), que
repite los nombres de las operaciones de la tienda, le ganaba al servicio
que las implementa. Lo que se entrega al modelo sigue siendo solo el código.

Cuando la pregunta nombra un identificador, la búsqueda es híbrida: además
de la similitud semántica, suben los fragmentos que se llaman así o que lo
usan (si la pregunta es "¿dónde se usa…?", pesa más quien lo usa que la
definición). En las preguntas con identificadores de psf/requests, Hit@5
pasó de 80% a 93% y el fragmento correcto llega al modelo en el 100% de los
casos (antes 87%); los demás conjuntos no cambiaron.

Los conjuntos son chicos, así que los números sirven para comparar
configuraciones, no como garantía general.

Si cambias `embedding_model` en `config.yaml`, el próximo `beacon sync`
reconstruye el índice solo (los vectores de modelos distintos no son
comparables), y mientras tanto las consultas responden con un aviso claro.

## Calidad de las respuestas (medida)

`tools/eval_answers.py` llama al modelo real y revisa cada respuesta con
criterios verificables, sin juicio humano: si menciona los datos clave de la
pregunta, si cita el archivo donde está la respuesta, si ante una pregunta
sobre algo que no existe en el repositorio dice que no hay información (sin
agregar código de relleno), si escribe identificadores que no aparecen en
el contexto entregado (posible invención) y si responde en español.

```bash
python tools/eval_answers.py <project_id> eval/answers-requests.yaml
```

Sobre psf/requests (12 preguntas respondibles y 4 que no lo son), con
llama3:8b:

| Configuración | Contenido | Cita el archivo | Ambos | Falso rechazo | Rechazo correcto |
|---|---|---|---|---|---|
| Prompt anterior | 75% | 33% | 33% | 8% | 100% |
| Reglas de cita y de rechazo ajustadas | 75% | 92% | 67% | 0% | 100% |
| + fragmentos rotulados por ruta y regla de premisas falsas (actual) | 100% | 83-92% | 83-92% | 0% | 100% |

Qué mostró cada medición:

- El modelo citaba "el fragmento 3" en vez del archivo (el usuario no ve esa
  numeración) y a veces cerraba una respuesta correcta con la frase de "no
  encontré información". Pedirle otra cosa en el prompt ayudó, pero lo
  resolvió quitar el número: cada fragmento llega rotulado con su ruta y
  sus líneas, que es lo que el modelo copia al citar.
- Ante una pregunta con una premisa falsa ("¿qué modelo de aprendizaje
  automático usa requests para predecir la codificación?") el modelo la
  aceptaba y completaba con lo que sabía de una biblioteca externa. Una
  regla explícita para ese caso lo corrigió.
- Con la temperatura por defecto, la misma pregunta daba resultados
  distintos entre corridas (el contenido correcto variaba entre 67% y 92%),
  lo que impedía comparar cambios. Las respuestas se generan con
  temperatura 0,2 y semilla fija; aun así, entre dos corridas iguales una
  respuesta puede cambiar, de ahí el rango en la tabla.

Como control, el conjunto `eval/answers-microservices-demo.yaml` se
escribió después de estos ajustes y no se usó para ninguno. Ahí los
resultados son más bajos: contenido 70%, cita 60%, ambos 50%, sin rechazos
indebidos y con 100% de rechazos correctos. Las respuestas aciertan en lo
principal pero son breves y a menudo no citan el archivo; es un límite del
modelo de 8B parámetros que conviene conocer (la interfaz muestra igual las
fuentes de cada respuesta). Una respuesta tarda unos 5 segundos con GPU de
8 GB.

## Tests

```bash
pip install -r requirements-dev.txt   # una vez: agrega pytest
pytest tests/              # backend

cd frontend
npm test                   # frontend (Vitest + Testing Library)
```

## Seguridad — limitaciones conocidas

Beacon está pensado para uso local o en red interna, por un desarrollador o equipo — **no** para exponerse directamente a internet:

- **La API no tiene autenticación por defecto.** Cualquiera con acceso de red al puerto puede leer/escribir configuración, registrar proyectos y ver código indexado. Se puede activar una clave simple con la variable de entorno `BEACON_API_KEY` (ver más abajo) — sirve para no dejarlo abierto a cualquiera en la misma red, pero no reemplaza un proxy de autenticación real si lo vas a exponer más allá de `localhost`.
- **Sin rate limiting.** No hay límite de frecuencia en `sync`/`query`; en uso interno normal no es un problema, pero no está pensado para tráfico público.
- El explorador de carpetas (`/system/browse-dirs`) está acotado al directorio home del usuario del proceso, y el clonado de repos valida el esquema de la URL (solo http(s)/ssh) — pero ambos asumen que quien llega a la API ya es de confianza, dado el punto anterior.
- `/system/available-models?ollama_host=...` hace que el servidor consulte el host indicado (para poblar la lista de modelos en Configuración). Con la API abierta, eso permite usar a Beacon para hacer peticiones HTTP hacia otras máquinas de su red; otra razón para activar `BEACON_API_KEY` si Beacon no corre solo en `localhost`.

Más detalle de cada decisión en [docs/ARQUITECTURA.md](docs/ARQUITECTURA.md).

### Activar la clave de API (opcional)

```bash
# Sin Docker
BEACON_API_KEY=algo-secreto beacon serve      # Linux/Mac
$env:BEACON_API_KEY="algo-secreto"; beacon serve   # PowerShell

# Con Docker: crea un archivo .env junto a docker-compose.yml
echo "BEACON_API_KEY=algo-secreto" > .env
docker compose up -d
```

Con la variable seteada, todos los endpoints de datos/configuración exigen el header `X-API-Key`. La UI web te deja ingresar la misma clave en **Configuración > Seguridad** (se guarda solo en tu navegador, vía `localStorage`). Sin la variable seteada (default), la API queda abierta como hasta ahora.

## Exportar/importar proyectos

Para llevar un proyecto ya indexado a otra máquina (útil para una demo o defensa, sin depender de reindexar en vivo ni de que Ollama responda rápido ese día):

```bash
beacon export <project_id> --output mi-proyecto.beacon.zip

# en la otra máquina:
beacon import mi-proyecto.beacon.zip
# o con otro id/ruta si cambió de máquina:
beacon import mi-proyecto.beacon.zip --id otro-id --repo-path /ruta/nueva/al/repo
```

El `.zip` incluye el índice de ChromaDB y la documentación generada — las consultas (`ask`/`query`) funcionan inmediatamente después de importar. `sync`/`docs` incrementales van a necesitar que `repo_path` apunte a un clon real del repo en la máquina destino.

## Documentación técnica

- [docs/ARQUITECTURA.md](docs/ARQUITECTURA.md) — pipeline, decisiones de diseño, estructura del código.

## Licencia

MIT — ver [LICENSE](LICENSE).
