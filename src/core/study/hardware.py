"""Machine data for the study: only what is needed to know how Beacon performs per configuration (CPU, RAM, free disk, GPUs, operating system). Never user name, paths, IP or machine name."""

import os
import platform
import shutil
import subprocess
import sys
from typing import List, Optional


def _os_name() -> str:
    """platform.release() says 'Windows 10' on Windows 11 too: they are told apart by the build number."""
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
    """Output of `nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits`."""
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
    return []  # no detectable NVIDIA GPU (there may be another GPU this check cannot see)


def collect_hardware(disk_path: str = ".") -> dict:
    """Machine snapshot. `disk_path` is only used to measure free space; the path is not stored."""
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
    try:
        import git
        from pathlib import Path
        return git.Repo(Path(__file__).resolve().parents[3]).head.commit.hexsha[:10]
    except Exception:
        return ""
