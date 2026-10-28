"""One-step setup: virtual environment, dependencies, web interface and models.

Run from the project folder with any Python 3.10-3.12:  python tools/install.py
Afterwards use the launcher in the project root (beacon.cmd on Windows, ./beacon elsewhere);
the virtual environment does not need to be activated.
"""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENV = ROOT / ".venv"
MODELS = ("qwen3-embedding:0.6b", "llama3:8b")


def run(cmd, cwd=ROOT):
    print("  $", " ".join(str(c) for c in cmd))
    subprocess.run([str(c) for c in cmd], cwd=cwd, check=True, shell=sys.platform == "win32" and cmd[0] in ("npm", "ollama"))


def main() -> int:
    if not (3, 10) <= sys.version_info[:2] <= (3, 12):
        print(f"Python 3.10, 3.11 or 3.12 is required (found {sys.version.split()[0]}).")
        print("Run this script with a supported version, for example: py -3.11 tools/install.py (Windows) or python3.11 tools/install.py (Linux/macOS)")
        return 1

    print("1/4 Virtual environment and dependencies")
    python = VENV / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    usable = python.exists() and subprocess.run([str(python), "-m", "pip", "--version"], capture_output=True).returncode == 0
    if not usable:
        shutil.rmtree(VENV, ignore_errors=True)  # leftover of an earlier failed run
        try:
            run([sys.executable, "-m", "venv", VENV])
        except subprocess.CalledProcessError:
            shutil.rmtree(VENV, ignore_errors=True)
            print("The virtual environment could not be created.")
            if sys.platform.startswith("linux"):
                print("On Debian/Ubuntu install the missing package first: sudo apt install python3-venv")
            return 1
    run([python, "-m", "pip", "install", "-r", "requirements.txt"])
    run([python, "-m", "pip", "install", "-e", "."])

    config = ROOT / "config" / "config.yaml"
    if not config.exists():
        shutil.copy(ROOT / "config" / "config.example.yaml", config)

    print("2/4 Web interface")
    if shutil.which("npm"):
        run(["npm", "install"], cwd=ROOT / "frontend")
        run(["npm", "run", "build"], cwd=ROOT / "frontend")
    else:
        print("  npm not found: install Node 20+ and run this script again to get the web interface.")

    print("3/4 Models")
    if shutil.which("ollama"):
        for model in MODELS:
            run(["ollama", "pull", model])
    else:
        print("  Ollama not found: install it from https://ollama.com and pull", ", ".join(MODELS))

    print("4/4 Done. Open a console with the environment active:")
    print("  Windows:     .\\beacon")
    print("  Linux/macOS: ./beacon")
    print("Then use beacon directly (beacon study, beacon doctor...).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
