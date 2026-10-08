# Uso

## CLI

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

## Interfaz web

Con `beacon serve` corriendo (y la UI ya compilada, ver [instalacion-manual.md](instalacion-manual.md)), abre `http://127.0.0.1:8000`:

- **Chat**: preguntas en lenguaje natural sobre el código indexado. Conserva el historial de preguntas de la sesión (se reinicia al cambiar de proyecto) y muestra, junto a cada respuesta, sus fuentes separadas en coincidencia semántica y grafo de llamadas, con la similitud, las líneas y el código de cada una. La cabecera indica el modelo en uso y si Ollama responde; si una consulta falla, la pregunta se conserva y se puede reintentar.
- **Docs**: árbol de la documentación generada por archivo, con carpetas plegables y filtro de texto.
- **Configuración**: proyectos (registro por ruta local o clonado por URL desde GitHub/GitLab/Bitbucket, sincronización de varios a la vez, watcher automático opcional y eliminación con confirmación), proveedor y modelos de IA (detectados desde Ollama, con aviso si un modelo configurado no está instalado) y clave de API.

El tema claro u oscuro se elige en la barra lateral, y las tipografías vienen empaquetadas: la interfaz no hace peticiones a servicios externos.

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
