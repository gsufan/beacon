"""Pruebas de estrés: un repositorio de miles de archivos y consultas simultáneas.

Dos pasos:

  python tools/stress_test.py generar <carpeta> [--archivos 2000]
      Crea un repositorio git sintético (Python, Go y JavaScript) con
      funciones de nombres y cuerpos variados, siempre igual para la misma
      semilla, para poder repetir la medición.

  python tools/stress_test.py medir <carpeta> [--concurrencia 1 4 8] [--conservar]
      Registra el repositorio como proyecto `stress-test` y mide:
        1. indexación completa: fragmentos, tiempo, fragmentos por segundo,
           memoria máxima del proceso y espacio en disco;
        2. sincronización incremental tras modificar 20 archivos;
        3. latencia de la búsqueda (sin LLM), con y sin identificadores;
        4. consultas completas (búsqueda + LLM) simultáneas contra el
           servidor real, en cada nivel de concurrencia;
        5. consultas mientras el índice se reconstruye por completo.
      Al final borra el proyecto y sus datos, salvo con --conservar.

Requiere Ollama con los modelos configurados. Los números dependen del
equipo; el informe imprime el hardware relevante (GPU no incluida).
"""

import argparse
import json
import os
import platform
import random
import shutil
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

PROJECT_ID = "stress-test"
PORT = 8765

DOMAINS = ["billing", "inventory", "shipping", "auth", "catalog", "payments", "notifications",
           "reports", "users", "orders", "pricing", "search", "audit", "scheduler", "storage"]
ENTITIES = ["invoice", "item", "shipment", "token", "product", "charge", "message", "report", "account",
            "order", "discount", "query", "event", "job", "file", "customer", "warehouse", "coupon"]
OPERATIONS = ["create", "update", "delete", "validate", "calculate", "find", "list", "sync", "export",
              "parse", "merge", "apply", "retry", "archive", "notify", "estimate", "reserve", "refund"]
DETAILS = ["total", "status", "limit", "batch", "cache", "window", "threshold", "summary", "history"]


# ------------------------------------------------------------------ generar ----

def _names(rng, count):
    names = set()
    while len(names) < count:
        names.add((rng.choice(OPERATIONS), rng.choice(ENTITIES), rng.choice(DETAILS)))
    return sorted(names)


def _python_file(rng, domain):
    funcs = _names(rng, rng.randint(4, 12))
    out = [f'"""{domain.capitalize()} helpers."""', "", "import math", "", f"MAX_{domain.upper()}_ITEMS = {rng.randint(10, 500)}", ""]
    defined = []
    for op, ent, det in funcs:
        name = f"{op}_{ent}_{det}"
        call = f"    extra = {rng.choice(defined)}(values)\n" if defined and rng.random() < 0.4 else "    extra = 0\n"
        out.append(
            f"\ndef {name}(values, factor={rng.randint(2, 9)}):\n"
            f'    """{op.capitalize()} the {det} of a {ent} for the {domain} module."""\n'
            f"    if not values:\n        return 0\n"
            f"{call}"
            f"    {det} = sum(v * factor for v in values if v > {rng.randint(0, 5)})\n"
            f"    if {det} > MAX_{domain.upper()}_ITEMS:\n"
            f"        {det} = math.floor({det} / factor)\n"
            f"    return {det} + extra\n")
        defined.append(name)
    return "\n".join(out) + "\n"


def _go_file(rng, domain):
    funcs = _names(rng, rng.randint(4, 12))
    out = [f"package {domain}", "", 'import "errors"', ""]
    defined = []
    for op, ent, det in funcs:
        name = f"{op.capitalize()}{ent.capitalize()}{det.capitalize()}"
        call = f"\textra := {rng.choice(defined)}(values)\n" if defined and rng.random() < 0.4 else "\textra := 0\n"
        out.append(
            f"// {name} {op}s the {det} of a {ent} in {domain}.\n"
            f"func {name}(values []int) int {{\n"
            f"\tif len(values) == 0 {{\n\t\treturn 0\n\t}}\n"
            f"{call}"
            f"\t{det} := 0\n\tfor _, v := range values {{\n\t\tif v > {rng.randint(0, 5)} {{\n\t\t\t{det} += v * {rng.randint(2, 9)}\n\t\t}}\n\t}}\n"
            f"\treturn {det} + extra\n}}\n")
        defined.append(name)
    out.append(f'var Err{domain.capitalize()} = errors.New("{domain} failed")\n')
    return "\n".join(out)


def _js_file(rng, domain):
    funcs = _names(rng, rng.randint(4, 12))
    out = [f"// {domain} service helpers", "'use strict';", ""]
    defined = []
    for op, ent, det in funcs:
        name = f"{op}{ent.capitalize()}{det.capitalize()}"
        call = f"  const extra = {rng.choice(defined)}(values);\n" if defined and rng.random() < 0.4 else "  const extra = 0;\n"
        out.append(
            f"/** {op} the {det} of a {ent} for {domain}. */\n"
            f"function {name}(values, factor = {rng.randint(2, 9)}) {{\n"
            f"  if (!values.length) return 0;\n"
            f"{call}"
            f"  const {det} = values.filter((v) => v > {rng.randint(0, 5)}).reduce((a, v) => a + v * factor, 0);\n"
            f"  return {det} + extra;\n}}\n")
        defined.append(name)
    out.append("module.exports = { " + ", ".join(f"{o}{e.capitalize()}{d.capitalize()}" for o, e, d in funcs) + " };\n")
    return "\n".join(out)


def generate(target: Path, files: int, seed: int = 7):
    import git
    if target.exists():
        raise SystemExit(f"{target} ya existe; elige otra carpeta o bórrala antes.")
    rng = random.Random(seed)
    makers = [(".py", _python_file), (".go", _go_file), (".js", _js_file)]
    for i in range(files):
        domain = DOMAINS[i % len(DOMAINS)]
        ext, maker = makers[i % len(makers)]
        path = target / "services" / domain / f"{domain}_{i:05d}{ext}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(maker(rng, domain), encoding="utf-8")
    repo = git.Repo.init(target)
    repo.git.add(A=True)
    repo.index.commit("repositorio sintético para pruebas de estrés")
    print(f"Generado {target}: {files} archivos.")


# -------------------------------------------------------------------- medir ----

def _peak_memory_mb() -> float:
    """Memoria máxima (working set / RSS) usada hasta ahora por este proceso."""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
        counters = Counters()
        counters.cb = ctypes.sizeof(Counters)
        kernel32, psapi = ctypes.WinDLL("kernel32"), ctypes.WinDLL("psapi")
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE  # sin esto el handle se trunca a 32 bits
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb)
        return counters.PeakWorkingSetSize / 2**20
    import resource
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / 2**20 if sys.platform == "darwin" else peak / 1024


def _dir_size_mb(path: Path) -> float:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) / 2**20


def _pct(values, p):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(p / 100 * (len(ordered) - 1)))]


def _post(path, payload, timeout=600):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw or b"null")
        except ValueError:  # ej. un 500 con texto plano
            return e.code, raw.decode("utf-8", "replace")


def _get(path, timeout=30):
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}{path}", timeout=timeout) as resp:
        return resp.status, json.loads(resp.read() or b"null")


def _wait_server():
    for _ in range(120):
        try:
            if _get("/healthz")[0] == 200:
                return
        except OSError:
            pass
        time.sleep(0.5)
    raise SystemExit("El servidor no respondió en 60 s.")


def _concurrent_queries(questions, level):
    """`level` consultas a la vez, en dos tandas; devuelve latencias y errores."""
    latencies, errors, lock = [], [], threading.Lock()

    def worker(question):
        t = time.perf_counter()
        status, body = _post(f"/projects/{PROJECT_ID}/query", {"question": question, "top_k": 5, "language": "es"})
        with lock:
            if status == 200 and body.get("answer"):
                latencies.append(time.perf_counter() - t)
            else:
                errors.append(f"{status}: {str(body)[:120]}")

    for batch in range(2):
        threads = [threading.Thread(target=worker, args=(questions[(batch * level + i) % len(questions)],))
                   for i in range(level)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
    return latencies, errors


def measure(repo: Path, levels, keep: bool):
    import git

    from core import services
    from core.config import load_config
    from core.engine.rag_engine import RAGEngine
    from core.projects import get_project

    report = {"equipo": {"so": platform.platform(), "cpu": platform.processor(), "nucleos": os.cpu_count(),
                         "python": platform.python_version()}}
    ai = load_config().ai_provider
    report["modelos"] = {"embeddings": ai.embedding_model, "llm": ai.llm_model}
    server = None
    try:
        services.register_project(project_id=PROJECT_ID, name="Prueba de estrés", repo_path=str(repo))
        project = get_project(PROJECT_ID)

        # 1. Indexación completa
        print("1/5 indexación completa...", flush=True)
        t = time.perf_counter()
        result = services.sync_project(PROJECT_ID, docs=False, pull=False).index
        elapsed = time.perf_counter() - t
        files = len([p for p in repo.rglob("*") if p.is_file() and ".git" not in p.parts])
        report["indexacion"] = {
            "archivos": files, "fragmentos": result["chunks_insertados"], "segundos": round(elapsed, 1),
            "fragmentos_por_segundo": round(result["chunks_insertados"] / elapsed, 1),
            "memoria_maxima_mb": round(_peak_memory_mb()), "disco_mb": round(_dir_size_mb(Path(project.chroma_dir)), 1)}
        print("   ", report["indexacion"], flush=True)

        # 2. Sincronización incremental
        print("2/5 sincronización incremental...", flush=True)
        repo_git = git.Repo(repo)
        changed = sorted(p for p in repo.rglob("*.py") if ".git" not in p.parts)[:20]
        for p in changed:
            p.write_text(p.read_text(encoding="utf-8") + "\n\ndef added_for_stress_test(x):\n    return x * 2\n",
                         encoding="utf-8")
        repo_git.git.add(A=True)
        repo_git.index.commit("cambio de 20 archivos")
        t = time.perf_counter()
        inc = services.sync_project(PROJECT_ID, docs=False, pull=False).index
        report["incremental"] = {"archivos_modificados": len(changed), "segundos": round(time.perf_counter() - t, 1),
                                 "fragmentos_reindexados": inc["chunks_insertados"]}
        print("   ", report["incremental"], flush=True)

        # 3. Latencia de la búsqueda
        print("3/5 latencia de la búsqueda...", flush=True)
        engine = RAGEngine(project, ai)
        sample = engine.collection.get(limit=400, include=["metadatas"])["metadatas"]
        names = [m["name"] for m in sample if m.get("chunk_type") == "function" and m.get("name")][:25]
        rng = random.Random(1)
        descriptive = [f"¿Cómo se calcula el {rng.choice(['total', 'estado', 'límite', 'historial'])} de "
                       f"{rng.choice(['una factura', 'un pedido', 'un envío', 'una cuenta', 'un reembolso'])} "
                       f"en {rng.choice(DOMAINS)}?" for _ in range(25)]
        with_ids = [f"¿Qué hace {n}?" for n in names]
        engine.retrieve("calentamiento")
        timings = {}
        for label, questions in (("descriptivas", descriptive), ("con_identificador", with_ids)):
            lat = []
            for q in questions:
                t = time.perf_counter()
                engine.select_context(q)
                lat.append(time.perf_counter() - t)
            timings[label] = {"consultas": len(lat), "p50_ms": round(1000 * statistics.median(lat)),
                              "p95_ms": round(1000 * _pct(lat, 95))}
        report["busqueda"] = timings
        print("   ", timings, flush=True)

        # 4. Consultas completas simultáneas contra el servidor real
        print("4/5 consultas simultáneas (servidor real)...", flush=True)
        server = subprocess.Popen([sys.executable, "-m", "core.cli", "serve", "--port", str(PORT)],
                                  cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        _wait_server()
        _post(f"/projects/{PROJECT_ID}/query", {"question": "calentamiento del modelo", "top_k": 5})
        report["concurrencia"] = {}
        for level in levels:
            lat, errs = _concurrent_queries(descriptive + with_ids, level)
            report["concurrencia"][str(level)] = {
                "consultas": len(lat) + len(errs), "errores": len(errs), "detalle_errores": errs[:3],
                "p50_s": round(statistics.median(lat), 1) if lat else None,
                "max_s": round(max(lat), 1) if lat else None}
            print(f"    {level} simultáneas:", report["concurrencia"][str(level)], flush=True)

        # 5. Consultas durante una reconstrucción completa
        print("5/5 consultas durante una reconstrucción completa...", flush=True)
        # El caso real: alguien corre `beacon sync --full` en una consola
        # mientras el servidor atiende consultas desde la interfaz.
        rebuild = subprocess.Popen([sys.executable, "-m", "core.cli", "sync", PROJECT_ID, "--full", "--no-pull"],
                                   cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        during = []
        while rebuild.poll() is None:
            t = time.perf_counter()
            code, body = _post(f"/projects/{PROJECT_ID}/query", {"question": rng.choice(with_ids), "top_k": 5})
            during.append({"status": code, "segundos": round(time.perf_counter() - t, 1),
                           "fuentes": len(body.get("sources", [])) if isinstance(body, dict) else 0,
                           "error": None if code == 200 else str(body)[:150]})
        report["durante_reconstruccion"] = {
            "codigo_salida_sync": rebuild.returncode, "consultas": len(during),
            "ok": sum(d["status"] == 200 for d in during),
            "sin_fuentes": sum(d["status"] == 200 and d["fuentes"] == 0 for d in during),
            "errores": [d["error"] for d in during if d["error"]][:3]}
        print("   ", report["durante_reconstruccion"], flush=True)
    finally:
        if server:
            server.terminate()
            server.wait(timeout=30)
        if not keep:
            try:
                services.unregister_project(PROJECT_ID, purge_data=True)
            except Exception as e:  # el informe importa más que la limpieza
                print(f"No se pudo borrar el proyecto de prueba: {e}")

    out = ROOT / "eval" / "stress_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nInforme guardado en {out.relative_to(ROOT)}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generar")
    g.add_argument("carpeta", type=Path)
    g.add_argument("--archivos", type=int, default=2000)
    m = sub.add_parser("medir")
    m.add_argument("carpeta", type=Path)
    m.add_argument("--concurrencia", type=int, nargs="+", default=[1, 4, 8])
    m.add_argument("--conservar", action="store_true", help="No borrar el proyecto de prueba al terminar.")
    args = parser.parse_args()
    if args.cmd == "generar":
        generate(args.carpeta.resolve(), args.archivos)
    else:
        measure(args.carpeta.resolve(), args.concurrencia, args.conservar)


if __name__ == "__main__":
    main()
