# Instalación manual (sin Docker)

Todo se hace desde una consola; no hace falta Docker.

## Requisitos

- **Python 3.10, 3.11 o 3.12** (recomendado 3.11, que es la versión que usan
  Docker y la CI). Python 3.13 o superior **no funciona todavía**: la
  dependencia `tree-sitter-languages` no publica paquetes para esas versiones y
  `pip install` falla con *"No matching distribution found"*. En Windows puedes
  tener varias versiones instaladas y elegir con `py -3.11`.
- [Node.js](https://nodejs.org/) 20+ (para compilar la UI web y correr sus tests)
- [Ollama](https://ollama.com/) instalado y corriendo (la app de escritorio lo
  deja corriendo en segundo plano; si no, `ollama serve` en otra consola)
- Git

## Pasos (Windows, PowerShell)

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
  (ver la nota sobre terminales en [uso.md](uso.md)).

## Pasos (Linux / macOS)

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

**Usarlo solo o compartirlo con un equipo, sin Docker.** Por defecto
`beacon serve` escucha solo en `127.0.0.1`, es decir, para quien lo corre. Para
que otras personas de la red local lo usen desde su navegador, levántalo con
`beacon serve --host 0.0.0.0` (y activa `BEACON_API_KEY`, ver [seguridad.md](seguridad.md), porque
con la API abierta cualquiera en la red puede usarla). Los comandos de la CLI
(`beacon ask`, `sync`, `docs`) no necesitan el servidor y funcionan en un solo
equipo; el watcher automático sí requiere `beacon serve`.
