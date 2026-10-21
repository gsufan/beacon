"""Datos del equipo para el estudio. Solo lo mínimo para saber cómo rinde Beacon en cada configuración:
CPU, RAM, disco libre, GPU(s) y sistema operativo. Nunca nombre de usuario, rutas, IP ni nombre del equipo."""

import os
import platform
import shutil
import subprocess
import sys
from typing import List, Optional


def _os_name() -> str:
    """platform.release() dice 'Windows 10' también en Windows 11: se distingue por el número de compilación."""
    if sys.platform == "win32":
        try:
            return "Windows 11" if sys.getwindowsversion().build >= 22000 else "Windows 10"
        except Exception:
            pass
    return f"{platform.system()} {platform.release()}"


def _cpu_name() -> str:
    try:
        if sys.platform == "win32":
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
                return str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
        if sys.platform == "darwin":
            return subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True,
                                  timeout=5).stdout.strip() or platform.processor()
        with open("/proc/cpuinfo", encoding="utf-8") as f:
            for line in f:
                if line.lower().startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return platform.processor() or "desconocido"


def _ram_gb() -> Optional[float]:
    try:
        if sys.platform == "win32":
            import ctypes

            class _Mem(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]

            mem = _Mem()
            mem.dwLength = ctypes.sizeof(_Mem)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(mem))
            return round(mem.ullTotalPhys / 1024 ** 3, 1)
        if sys.platform == "darwin":
            out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=5).stdout
            return round(int(out.strip()) / 1024 ** 3, 1)
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return round(int(line.split()[1]) / 1024 ** 2, 1)
    except Exception:
        pass
    return None


def parse_nvidia_smi(output: str) -> List[dict]:
    """Salida de `nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits`."""
    gpus = []
    for line in output.splitlines():
        if "," not in line:
            continue
        name, mem = line.rsplit(",", 1)
        try:
            gpus.append({"modelo": name.strip(), "vram_gb": round(float(mem.strip()) / 1024, 1)})
        except ValueError:
            continue
    return gpus


def _gpus() -> List[dict]:
    try:
        result = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                                capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            return parse_nvidia_smi(result.stdout)
    except Exception:
        pass
    return []  # sin GPU NVIDIA detectable (puede haber otra GPU que este chequeo no ve)


def collect_hardware(disk_path: str = ".") -> dict:
    """Foto del equipo. `disk_path` solo se usa para medir el espacio libre; la ruta no se guarda."""
    try:
        free_gb = round(shutil.disk_usage(disk_path).free / 1024 ** 3, 1)
    except Exception:
        free_gb = None
    return {
        "cpu_modelo": _cpu_name(),
        "cpu_nucleos": os.cpu_count(),
        "ram_gb": _ram_gb(),
        "disco_libre_gb": free_gb,
        "gpus": _gpus(),
        "so": _os_name(),
    }


def beacon_commit() -> str:
    """Commit de Beacon que se está ejecutando (vacío si no es una copia de git)."""
    try:
        import git
        from pathlib import Path
        return git.Repo(Path(__file__).resolve().parents[3]).head.commit.hexsha[:10]
    except Exception:
        return ""
