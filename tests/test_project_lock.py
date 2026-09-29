"""Lock por proyecto entre procesos: el servidor (con su watcher) y la CLI
no deben escribir el mismo índice al mismo tiempo. Los tests usan un
proceso hijo real, porque justamente eso es lo que _claim_sync no cubría."""

import subprocess
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402
from typer.testing import CliRunner  # noqa: E402

import core.api as api  # noqa: E402
import core.cli as cli_module  # noqa: E402
import core.services as services  # noqa: E402
from core.config import AIProviderConfig, AppConfig, ProjectEntry  # noqa: E402
from core.project_lock import LOCK_FILENAME, ProjectBusyError, ensure_not_busy, project_lock  # noqa: E402

SRC = str(Path(__file__).resolve().parents[1] / "src")
AI = AIProviderConfig(provider="ollama", ollama_host="http://x", embedding_model="e", llm_model="l")


class _Holder:
    """Proceso hijo que toma el lock y lo mantiene hasta que se le cierra stdin."""

    def __init__(self, project_dir: Path):
        code = textwrap.dedent(f"""
            import sys
            sys.path.insert(0, {SRC!r})
            from core.project_lock import project_lock
            with project_lock({str(project_dir)!r}):
                print("locked", flush=True)
                sys.stdin.read()
        """)
        self.proc = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, text=True)
        assert self.proc.stdout.readline().strip() == "locked"

    def release(self):
        self.proc.stdin.close()
        self.proc.wait(timeout=10)

    def kill(self):
        self.proc.kill()
        self.proc.wait(timeout=10)


def test_lock_is_exclusive_across_processes(tmp_path):
    holder = _Holder(tmp_path / "demo")
    try:
        with pytest.raises(ProjectBusyError):
            with project_lock(tmp_path / "demo"):
                pass
    finally:
        holder.release()
    with project_lock(tmp_path / "demo"):  # liberado: ahora sí se puede tomar
        pass


def test_lock_is_released_if_holder_process_dies(tmp_path):
    holder = _Holder(tmp_path / "demo")
    holder.kill()  # simula Ctrl+C / cierre de la consola a mitad de un sync
    # Windows libera los locks de un proceso terminado de forma asíncrona
    # ("puede demorar según los recursos del sistema"), así que se reintenta
    # unos segundos; lo que importa es que se libera solo, sin borrar nada.
    import time
    deadline = time.monotonic() + 10
    while True:
        try:
            with project_lock(tmp_path / "demo"):
                break
        except ProjectBusyError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.2)


def test_ensure_not_busy_on_missing_dir_does_not_create_it(tmp_path):
    ensure_not_busy(tmp_path / "no-existe")
    assert not (tmp_path / "no-existe").exists()


def _fake_project(monkeypatch, project_dir, source_type="local"):
    """Proyecto falso para los servicios: sin config real ni Ollama."""
    class FakeProject:
        name = "Demo"
        repo_path = str(project_dir)
        chroma_dir = str(project_dir / "chroma_db")

    entry = ProjectEntry(id="demo", name="Demo", repo_path=str(project_dir), source_type=source_type, auto_watch=True)
    monkeypatch.setattr(services, "get_project", lambda pid: FakeProject())
    monkeypatch.setattr(services, "_project_entry", lambda pid: entry)
    monkeypatch.setattr(services, "load_config", lambda: AppConfig(ai_provider=AI, projects=[entry]))
    return FakeProject, entry


def test_cli_sync_refuses_while_another_process_syncs(monkeypatch, tmp_path):
    project_dir = tmp_path / "demo"
    FakeProject, _ = _fake_project(monkeypatch, project_dir)
    monkeypatch.setattr(cli_module, "_resolve_or_exit", lambda pid: FakeProject())

    def must_not_run(*a, **k):
        raise AssertionError("no debe indexar mientras otro proceso tiene el lock")

    monkeypatch.setattr(services, "CodebaseIndexer", must_not_run)
    holder = _Holder(project_dir)
    try:
        result = CliRunner().invoke(cli_module.app, ["sync", "demo"])
    finally:
        holder.release()
    assert result.exit_code == 1
    assert "otro proceso" in result.output


def test_api_sync_reports_error_while_cli_holds_lock(monkeypatch, tmp_path):
    project_dir = tmp_path / "demo"
    _fake_project(monkeypatch, project_dir)
    monkeypatch.setattr(services, "CodebaseIndexer", lambda *a, **k: pytest.fail("no debe indexar"))
    holder = _Holder(project_dir)
    try:
        api._run_sync("demo-lock")
        status = api._sync_status["demo-lock"]
    finally:
        holder.release()
        api._sync_status.pop("demo-lock", None)
    assert status["status"] == "error"
    assert "otro proceso" in status["detail"]


def test_auto_watch_skips_project_locked_by_cli_without_pulling(monkeypatch, tmp_path):
    project_dir = tmp_path / "demo"
    _, entry = _fake_project(monkeypatch, project_dir, source_type="git")
    monkeypatch.setattr(api, "load_config", lambda: AppConfig(ai_provider=AI, projects=[entry]))
    calls = []
    monkeypatch.setattr(services, "pull_from_remote", lambda e: calls.append("pull"))
    monkeypatch.setattr(services, "CodebaseIndexer", lambda *a, **k: calls.append("sync"))
    holder = _Holder(project_dir)
    try:
        api._auto_watch_tick()
    finally:
        holder.release()
    assert calls == []  # ni git pull ni indexado: el pull va dentro del lock
    assert "demo" not in api._sync_status  # claim liberado, sin marcar error


def test_git_project_sync_pulls_inside_the_lock(monkeypatch, tmp_path):
    project_dir = tmp_path / "demo"
    _fake_project(monkeypatch, project_dir, source_type="git")
    seen = {}

    def pull(entry):
        with pytest.raises(ProjectBusyError):  # otro proceso no puede entrar mientras se hace el pull
            with project_lock(project_dir):
                pass
        seen["pull"] = True

    class FakeIndexer:
        def __init__(self, *a, **k):
            self.project = type("P", (), {"chroma_dir": str(project_dir / "chroma_db")})()

        def sync(self, **kwargs):
            return {"status": "sin_cambios"}

    monkeypatch.setattr(services, "pull_from_remote", pull)
    monkeypatch.setattr(services, "CodebaseIndexer", FakeIndexer)
    result = services.sync_project("demo", docs=False)
    assert seen == {"pull": True} and result.pulled


def test_export_skips_lock_file(monkeypatch, tmp_path):
    import zipfile
    project_dir = tmp_path / "demo"
    (project_dir / "docs").mkdir(parents=True)
    (project_dir / "docs" / "a.md").write_text("# a")
    (project_dir / LOCK_FILENAME).write_text("")
    monkeypatch.setattr(cli_module, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(cli_module, "load_config", lambda: AppConfig(
        ai_provider=AI, projects=[ProjectEntry(id="demo", name="d", repo_path=".")]))
    out = tmp_path / "demo.zip"
    result = CliRunner().invoke(cli_module.app, ["export", "demo", "-o", str(out)])
    assert result.exit_code == 0, result.output
    names = zipfile.ZipFile(out).namelist()
    assert "data/docs/a.md" in [n.replace("\\", "/") for n in names]
    assert not any(n.endswith(LOCK_FILENAME) for n in names)
