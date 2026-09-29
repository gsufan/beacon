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

## Instalación con Docker (recomendada)

Levanta Beacon **y** Ollama en contenedores, sin instalar Python ni Node en tu máquina.

```bash
git clone https://github.com/<tu-usuario>/beacon.git
cd beacon
cp config/config.example.yaml config/config.yaml
```

Edita `config/config.yaml`: deja `ollama_host: http://ollama:11434` (el nombre `ollama` lo resuelve la red interna de Docker Compose — no uses `localhost` ahí) y agrega el/los repo(s) que quieras indexar.

```bash
docker compose up -d --build

# primera vez: descargar los modelos dentro del contenedor de Ollama
docker compose exec ollama ollama pull nomic-embed-text
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
- [Node.js](https://nodejs.org/) 18+ (solo para compilar la UI web)
- [Ollama](https://ollama.com/) instalado y corriendo (la app de escritorio lo
  deja corriendo en segundo plano; si no, `ollama serve` en otra consola)
- Git

### Pasos (Windows, PowerShell)

```powershell
# 1. Clonar y entrar al proyecto
git clone <url-del-repositorio> beacon
cd beacon

# 2. Entorno virtual con Python 3.11 + dependencias del backend
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -e .

# 3. Modelos de Ollama (una sola vez, ~5 GB en total)
ollama pull nomic-embed-text
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
git clone <url-del-repositorio> beacon && cd beacon
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
ollama pull nomic-embed-text && ollama pull llama3:8b
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

## Tests

```bash
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
