[Español](../es/instalacion-manual.md) | **English**

# Manual installation (without Docker)

Everything is done from a console; Docker is not needed.

## Requirements

- **Python 3.10, 3.11 or 3.12** (3.11 recommended, which is the version used by
  Docker and the CI). Python 3.13 or later **does not work yet**: the
  `tree-sitter-languages` dependency does not publish packages for those
  versions and `pip install` fails with *"No matching distribution found"*. On
  Windows you can have several versions installed and choose with `py -3.11`.
- [Node.js](https://nodejs.org/) 20+ (to build the web UI and run its tests)
- [Ollama](https://ollama.com/) installed and running (the desktop app leaves
  it running in the background; if not, `ollama serve` in another console)
- Git

## Quick way (recommended)

One script does the whole setup: virtual environment, dependencies, `config/config.yaml` (copied from the example), web interface (if Node is installed) and the two Ollama models (if Ollama is installed).

```bash
git clone https://github.com/gsufan/beacon.git
cd beacon
python tools/install.py    # with Python 3.10-3.12, e.g. py -3.11 tools/install.py
.\beacon                   # opens a console with the environment active (Linux/macOS: ./beacon)
beacon doctor
```

The launcher (`beacon.cmd` on Windows, `beacon` on Linux/macOS) lives in the project root. Without arguments it opens a console with the virtual environment already active, so you can use `beacon ...` directly; with arguments it runs that command (for example `.\beacon doctor`). You never need to activate the environment by hand.

If you prefer to do each step yourself, or the script fails, follow the long process below.

## Step by step (Windows, PowerShell)

```powershell
# 1. Clone and enter the project
git clone https://github.com/gsufan/beacon.git
cd beacon

# 2. Virtual environment with Python 3.11 + backend dependencies
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -e .

# 3. Ollama models (one time only, ~5.3 GB in total)
ollama pull qwen3-embedding:0.6b
ollama pull llama3:8b

# 4. Base configuration
Copy-Item config\config.example.yaml config\config.yaml
# (Optional) tokens for private repos cloned by URL
Copy-Item config\credentials.example.yaml config\credentials.yaml

# 5. Check that everything is fine
beacon doctor

# 6. Register and index a repository
beacon add my-project --repo-path C:\path\to\repo    # or: --url https://github.com/user/repo.git
beacon sync my-project
beacon ask my-project "What does this project do?"

# 7. (Optional) Build and start the web UI
cd frontend
npm install
npm run build
cd ..
beacon serve        # http://127.0.0.1:8000 — leave this console open
```

Notes for Windows:

- If `Activate.ps1` gives a *"running scripts is disabled"* error, use
  `cmd.exe` and activate with `.venv\Scripts\activate.bat`, or do not activate
  the environment and call `.venv\Scripts\beacon.exe` directly.
- In `cmd.exe` copy with `copy` instead of `Copy-Item`.
- Run the CLI from PowerShell, cmd or Windows Terminal, not from Git Bash
  (see the note on terminals in [usage.md](usage.md)).

## Steps (Linux / macOS)

```bash
git clone https://github.com/gsufan/beacon.git && cd beacon
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
ollama pull qwen3-embedding:0.6b && ollama pull llama3:8b
cp config/config.example.yaml config/config.yaml
beacon doctor
beacon add my-project --repo-path ../my-project
beacon sync my-project
cd frontend && npm install && npm run build && cd ..   # optional: web UI
beacon serve
```

If you start `beacon serve` without having built the UI, the API works anyway
and the root (`http://127.0.0.1:8000`) shows how to build it.

**Using it alone or sharing it with a team, without Docker.** By default
`beacon serve` listens only on `127.0.0.1`, that is, for whoever runs it. For
other people on the local network to use it from their browser, start it with
`beacon serve --host 0.0.0.0` (and enable `BEACON_API_KEY`, see [security.md](security.md),
because with the API open anyone on the network can use it). The CLI commands
(`beacon ask`, `sync`, `docs`) do not need the server and work on a single
machine; the automatic watcher does require `beacon serve`.

## Using `beacon` from any folder

The `beacon` command is an executable created inside the virtual environment (`.venv\Scripts\beacon.exe` on Windows, `.venv/bin/beacon` on Linux and macOS) when you run `pip install -e .`. To use it from any folder without activating the environment, you have to add **that environment folder** to the `PATH`, not the project root (which contains no executable).

```powershell
# Windows, only for the current console
$env:Path = "C:\path\to\beacon\.venv\Scripts;" + $env:Path

# To make it permanent: Windows Settings > Environment variables > Path > New
```

```bash
# Linux / macOS (add to ~/.bashrc or ~/.zshrc to make it permanent)
export PATH="/path/to/beacon/.venv/bin:$PATH"
```

After that, `beacon projects` or `beacon ask <id> "question"` work from any folder. Keep in mind:

- **The configuration and the data are still the project's.** `beacon` looks for `config/config.yaml` and `data/` next to the code, not in the folder where it is run, so it always uses the same registered projects and indexes. The two configuration files can be changed with the `DEUDA_TECNICA_CONFIG` and `DEUDA_TECNICA_CREDENTIALS` environment variables; the `data/` folder has no equivalent variable.
- **Relative `repo_path` values are interpreted from the folder where the command is run**, not from the project's. A project registered with `../requests-main` is only found if the command is run from the project folder (or an equivalent one). To use `beacon` from any folder you must register the repositories with absolute paths (`beacon add <id> --repo-path C:\path\to\repo`, or edit `repo_path` with `beacon edit <id> --repo-path ...`).
- **`pipx install .` is not supported for now.** `pyproject.toml` does not declare the dependencies (they are in `requirements.txt`), so a non-editable installation is left without them and `beacon` fails at startup with `ModuleNotFoundError`.
