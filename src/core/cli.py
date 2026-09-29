"""
Punto de entrada único. Antes: 5 scripts sueltos, cada uno con su propia
consola muda. Ahora: un comando, con progreso real y mensajes claros.

Uso:
    beacon projects                        # lista proyectos registrados (Read)
    beacon add <id> --repo-path <ruta>     # registra un proyecto local ya clonado (Create)
    beacon add <id> --url <repo_url>       # o clona uno remoto y lo registra (Create)
    beacon edit <id> [--name] [--repo-path] [--auto-watch/--no-auto-watch]  # (Update)
    beacon remove <id> [--purge-data]      # desregistra un proyecto (Delete)
    beacon status <id>                     # estado del índice/documentación de un proyecto
    beacon doctor                          # chequeo de salud (Ollama, modelos, repos)
    beacon sync <id>                       # indexa + purga generado
    beacon docs <id>                       # genera documentación
    beacon ask <id> "pregunta"
    beacon export <id>                     # empaqueta el índice+docs en un .zip portable
    beacon import <archivo.zip>            # registra un proyecto desde un .zip exportado
    beacon serve                           # levanta la API + UI
"""

import json
import logging
import zipfile
from contextlib import nullcontext
from pathlib import Path
from typing import Optional

import git
import ollama
import typer
from rich.console import Console
from rich.progress import Progress, BarColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from core.config import (
    InvalidProjectIdError,
    ProjectAlreadyExistsError,
    ProjectEntry,
    add_project,
    set_auto_watch,
    update_project,
    load_config,
    validate_project_id,
)
from core.projects import (
    DATA_ROOT,
    InvalidRepoUrlError,
    list_projects,
    get_project,
    ProjectNotFoundError,
)
from core.project_lock import LOCK_FILENAME, ProjectBusyError
from core.engine.chroma_utils import bump_index_version
from core import services
from core.engine.indexer import CodebaseIndexer, IndexModelMismatchError
from core.engine.doc_generator import DocGenerator, IndexOutOfDateError
from core.engine.rag_engine import RAGEngine

app = typer.Typer(help="Beacon — plataforma de mitigación de deuda técnica")
console = Console()


def _progress_bar(description: str):
    return Progress(
        TextColumn(description), BarColumn(), TextColumn("{task.completed}/{task.total}"),
        TextColumn("· {task.fields[current_file]}"), TimeElapsedColumn(), console=console,
    )


def _progress_callback(progress, task):
    def on_progress(i, total, fp):
        if progress.tasks[task].total != total:
            progress.update(task, total=total)
        progress.update(task, completed=i, current_file=fp)
    return on_progress


@app.command("projects")
def cmd_list_projects():
    """Lista los proyectos registrados en config.yaml."""
    projects = list_projects()
    if not projects:
        console.print("[yellow]No hay proyectos registrados en config/config.yaml todavía.[/yellow]")
        raise typer.Exit()
    table = Table(title="Proyectos registrados")
    table.add_column("id"); table.add_column("nombre"); table.add_column("repo")
    for p in projects:
        table.add_row(p.id, p.name, p.repo_path)
    console.print(table)


@app.command("add")
def cmd_add(
    project_id: str,
    repo_path: str = typer.Option(None, "--repo-path", help="Ruta local a un repo git ya clonado."),
    url: str = typer.Option(None, "--url", help="URL de un repo remoto a clonar (alternativa a --repo-path)."),
    name: str = typer.Option(None, "--name", help="Nombre visible (por defecto, el id)."),
    auto_watch: bool = typer.Option(False, "--auto-watch/--no-auto-watch"),
    private: bool = typer.Option(False, "--private", help="Repo privado: pide un token de acceso (no se muestra al escribirlo)."),
):
    """Registra un proyecto nuevo: un repo local ya clonado (--repo-path),
    o clona uno remoto y lo registra (--url)."""
    token = None
    if private:
        if not url:
            console.print("[red]--private solo aplica a repos remotos (--url).[/red]")
            raise typer.Exit(code=1)
        token = typer.prompt("Token de acceso", hide_input=True)
    with console.status(f"Clonando {url}...") if url else nullcontext():
        entry = _run_safely(lambda: services.register_project(
            project_id, name=name, repo_path=repo_path, repo_url=url, auth_token=token, auto_watch=auto_watch,
        ), repo_path=repo_path or "")
    console.print(f"[green]OK[/green] Proyecto '{entry.id}' registrado. Corre: beacon sync {entry.id}")


@app.command("edit")
def cmd_edit(
    project_id: str,
    name: str = typer.Option(None, "--name", help="Nuevo nombre visible."),
    repo_path: str = typer.Option(None, "--repo-path", help="Nueva ruta local del repo."),
    auto_watch: Optional[bool] = typer.Option(None, "--auto-watch/--no-auto-watch", help="Activa/desactiva el watcher automático."),
):
    """Edita un proyecto ya registrado (nombre, repo_path y/o auto_watch)."""
    if name is None and repo_path is None and auto_watch is None:
        console.print("[yellow]No pasaste nada para editar — usa --name, --repo-path y/o --auto-watch/--no-auto-watch.[/yellow]")
        raise typer.Exit(code=1)
    if repo_path is not None and not Path(repo_path).exists():
        console.print(f"[red]'{repo_path}' no existe.[/red]")
        raise typer.Exit(code=1)

    changed = False
    if name is not None or repo_path is not None:
        changed = update_project(project_id, name=name, repo_path=repo_path) or changed
    if auto_watch is not None:
        changed = set_auto_watch(project_id, auto_watch) or changed

    if not changed:
        console.print(f"[red]Proyecto '{project_id}' no encontrado en config.yaml.[/red]")
        raise typer.Exit(code=1)
    console.print(f"[green]OK[/green] '{project_id}' actualizado.")


@app.command("remove")
def cmd_remove(
    project_id: str,
    purge_data: bool = typer.Option(False, "--purge-data", help="Además borra data/<id>/ (índice + docs) del disco."),
):
    """Desregistra un proyecto de config.yaml (y opcionalmente borra sus datos indexados)."""
    _run_safely(lambda: services.unregister_project(project_id, purge_data=purge_data))
    console.print(f"[green]OK[/green] '{project_id}' eliminado de config.yaml."
                  + (" Datos en data/ también borrados." if purge_data else " (los datos en data/ quedaron intactos — usa --purge-data para borrarlos)."))


@app.command("sync")
def cmd_sync(
    project_id: str,
    uncommitted: bool = typer.Option(False, "--uncommitted", help="Incluye cambios sin commitear (no registra commit)."),
    full: bool = typer.Option(False, "--full", help="Rehace el índice completo en vez de solo lo que cambió."),
    docs: bool = typer.Option(False, "--docs", help="Además actualiza la documentación (como el botón de la UI)."),
    pull: bool = typer.Option(True, "--pull/--no-pull", help="Traer cambios del remoto antes (solo proyectos clonados por URL)."),
):
    """Pone al día el índice de un proyecto (incremental). En proyectos
    clonados por URL, primero trae los cambios del remoto."""
    project = _resolve_or_exit(project_id)
    console.print(f"[bold]Sincronizando[/bold] {project.name}...")

    def _do_sync():
        with _progress_bar("Procesando") as progress:
            idx_task = progress.add_task("indexando", total=1, current_file="")
            doc_task = progress.add_task("documentando", total=1, current_file="", visible=docs)
            return services.sync_project(
                project_id, docs=docs, full=full, include_uncommitted=uncommitted, pull=pull,
                on_index_progress=_progress_callback(progress, idx_task),
                on_docs_progress=_progress_callback(progress, doc_task),
            )

    result = _run_safely(_do_sync, repo_path=project.repo_path)
    if result.pulled:
        console.print("[dim]Cambios del remoto traídos con git pull.[/dim]")
    console.print(f"[green]OK[/green] {result.index}")
    if result.generated_purged:
        console.print(f"[green]Purgados {result.generated_purged} chunks de código generado/vendor.[/green]")
    if result.docs is not None:
        console.print(f"[green]Documentación:[/green] {result.docs}")


@app.command("docs")
def cmd_docs(
    project_id: str,
    full: bool = typer.Option(False, "--full", help="Regenera toda la documentación en vez de solo lo que cambió."),
):
    """Genera/actualiza documentación .md incremental para un proyecto (requiere 'sync' al día)."""
    project = _resolve_or_exit(project_id)
    console.print(f"[bold]Generando documentación[/bold] para {project.name}...")

    def _do_docs():
        with _progress_bar("Documentando") as progress:
            task = progress.add_task("documentando", total=1, current_file="")
            return services.generate_docs(project_id, full=full, on_progress=_progress_callback(progress, task))

    result = _run_safely(_do_docs, repo_path=project.repo_path)
    console.print(f"[green]OK[/green] {result}")


@app.command("ask")
def cmd_ask(project_id: str, question: str, top_k: int = 5):
    """Consulta en lenguaje natural sobre un proyecto ya indexado."""
    project = _resolve_or_exit(project_id)
    cfg = load_config()
    engine = RAGEngine(project, cfg.ai_provider)

    if engine.collection.count() == 0:
        console.print(f"[red]El índice de '{project_id}' está vacío. Corre 'beacon sync {project_id}' primero.[/red]")
        raise typer.Exit(code=1)

    with console.status("Consultando..."):
        result = _run_safely(lambda: engine.ask(question, top_k=top_k), repo_path=project.repo_path)

    console.print(f"\n[bold]{result.answer}[/bold]\n")
    table = Table(title="Fuentes")
    table.add_column("archivo"); table.add_column("tipo"); table.add_column("nombre"); table.add_column("origen")
    for s in result.sources:
        origen = "grafo de llamadas" if s.expanded else f"semántico ({s.distance:.3f})"
        table.add_row(f"{s.file_path}:{s.start_line}-{s.end_line}", s.chunk_type, s.name, origen)
    console.print(table)


@app.command("export")
def cmd_export(project_id: str, output: str = typer.Option(None, "--output", "-o")):
    """Empaqueta el índice (ChromaDB) y la documentación de un proyecto en un .zip portable.

    Útil para llevar un proyecto ya indexado a otra máquina (ej. una demo o
    la defensa) sin depender de reindexar en vivo.
    """
    cfg = load_config()
    entry = next((p for p in cfg.projects if p.id == project_id), None)
    if entry is None:
        console.print(f"[red]Proyecto '{project_id}' no encontrado en config.yaml.[/red]")
        raise typer.Exit(code=1)

    project_dir = DATA_ROOT / project_id
    if not project_dir.exists():
        console.print(f"[red]No hay datos indexados para '{project_id}' todavía — corre 'beacon sync {project_id}' primero.[/red]")
        raise typer.Exit(code=1)

    output_path = Path(output) if output else Path(f"{project_id}.beacon.zip")
    manifest = {
        "id": entry.id, "name": entry.name, "repo_path": entry.repo_path,
        "source_type": entry.source_type, "repo_url": entry.repo_url,
    }
    with console.status(f"Empaquetando {project_id}..."):
        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
            for file in project_dir.rglob("*"):
                if file.is_file() and file.name != LOCK_FILENAME:
                    zf.write(file, arcname=str(Path("data") / file.relative_to(project_dir)))

    size_mb = output_path.stat().st_size / 1_048_576
    console.print(f"[green]OK[/green] Exportado a {output_path} ({size_mb:.1f} MB)")


@app.command("import")
def cmd_import(
    zip_path: str,
    project_id: str = typer.Option(None, "--id", help="Sobreescribe el id del manifest (por si ya existe)."),
    repo_path: str = typer.Option(None, "--repo-path", help="Sobreescribe repo_path (útil si cambió de máquina)."),
):
    """Registra un proyecto a partir de un .zip generado con 'beacon export'."""
    zpath = Path(zip_path)
    if not zpath.exists():
        console.print(f"[red]No existe el archivo '{zip_path}'.[/red]")
        raise typer.Exit(code=1)

    with zipfile.ZipFile(zpath) as zf:
        try:
            manifest = json.loads(zf.read("manifest.json"))
        except KeyError:
            console.print("[red]El .zip no tiene manifest.json — ¿fue generado con 'beacon export'?[/red]")
            raise typer.Exit(code=1)

        target_id = project_id or manifest["id"]
        # El id puede venir del manifest de un .zip ajeno: validarlo ANTES de
        # tocar el disco, o un id como "../x" extraería fuera de data/.
        _validate_id_or_exit(target_id)
        target_dir = (DATA_ROOT / target_id).resolve()
        if target_dir.exists():
            console.print(f"[red]Ya existe data/{target_id}/ — usa --id para elegir otro nombre, o borra esa carpeta primero.[/red]")
            raise typer.Exit(code=1)

        target_dir.mkdir(parents=True, exist_ok=True)
        with console.status(f"Extrayendo a data/{target_id}/..."):
            for member in zf.namelist():
                if member == "manifest.json" or not member.startswith("data/"):
                    continue
                dest = (target_dir / Path(member).relative_to("data")).resolve()
                if not dest.is_relative_to(target_dir):
                    continue  # zip malicioso/corrupto intentando escapar de target_dir
                dest.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as src, open(dest, "wb") as out:
                    out.write(src.read())
        bump_index_version(target_dir)  # un servidor ya levantado no debe usar datos previos de este id

    final_repo_path = repo_path or manifest["repo_path"]
    try:
        add_project(ProjectEntry(
            id=target_id, name=manifest["name"], repo_path=final_repo_path,
            source_type=manifest.get("source_type", "local"), repo_url=manifest.get("repo_url"),
        ))
    except ProjectAlreadyExistsError as e:
        console.print(f"[red]{e}[/red] [dim](los datos ya se copiaron a data/{target_id}/, pero no se registró en config.yaml)[/dim]")
        raise typer.Exit(code=1)

    console.print(f"[green]OK[/green] Importado como '{target_id}'. Prueba: beacon ask {target_id} \"...\"")
    if not Path(final_repo_path).exists():
        console.print(
            f"[yellow]![/yellow] 'repo_path' ({final_repo_path}) no existe en esta máquina — "
            "las consultas sobre lo ya indexado funcionan igual, pero 'sync'/'docs' incrementales "
            "van a fallar hasta que ajustes la ruta en config.yaml (o reimportes con --repo-path)."
        )


@app.command("status")
def cmd_status(project_id: str):
    """Muestra el estado del índice y la documentación de un proyecto."""
    project = _resolve_or_exit(project_id)
    cfg = load_config()

    def _do_status():
        indexer = CodebaseIndexer(project, cfg.ai_provider)
        generator = DocGenerator(project, cfg.ai_provider)
        return indexer.stats(), generator.stats()

    index_stats, doc_stats = _run_safely(_do_status, repo_path=project.repo_path)

    commit_actual = index_stats["commit_actual"][:8]
    ultimo_indexado = (index_stats["ultimo_commit_indexado"] or "nunca")[:8] if index_stats["ultimo_commit_indexado"] else "nunca"
    ultimo_documentado = (doc_stats["ultimo_commit_documentado"] or "nunca")[:8] if doc_stats["ultimo_commit_documentado"] else "nunca"

    table = Table(title=f"Estado — {project.name}")
    table.add_column("")
    table.add_column("valor")
    table.add_row("Chunks indexados", str(index_stats["total_chunks"]))
    table.add_row("Commit actual del repo", commit_actual)
    table.add_row("Último commit indexado", ultimo_indexado)
    table.add_row("Índice al día", "[green]sí[/green]" if ultimo_indexado == commit_actual else "[yellow]no — corre 'sync'[/yellow]")
    table.add_row("Último commit documentado", ultimo_documentado)
    table.add_row("Docs al día", "[green]sí[/green]" if ultimo_documentado == commit_actual else "[yellow]no — corre 'docs'[/yellow]")
    console.print(table)


@app.command("doctor")
def cmd_doctor():
    """Chequeo de salud: Ollama, modelos, y proyectos registrados."""
    cfg = load_config()
    ok_all = True

    console.print("[bold]Chequeando Ollama...[/bold]")
    try:
        client = ollama.Client(host=cfg.ai_provider.ollama_host)
        models_response = client.list()
        installed = {m["model"] for m in models_response.get("models", [])}
        console.print(f"  [green]✓[/green] Conectado en {cfg.ai_provider.ollama_host}")

        for label, model in [("embedding_model", cfg.ai_provider.embedding_model), ("llm_model", cfg.ai_provider.llm_model)]:
            match = any(model in m for m in installed)
            if match:
                console.print(f"  [green]✓[/green] {label} ({model}) está descargado")
            else:
                console.print(f"  [red]✗[/red] {label} ({model}) NO está descargado — corre: ollama pull {model}")
                ok_all = False
    except Exception as e:
        console.print(f"  [red]✗[/red] No se pudo conectar a Ollama en {cfg.ai_provider.ollama_host}: {e}")
        console.print("  [dim]¿Corriste 'ollama serve'?[/dim]")
        ok_all = False

    console.print("\n[bold]Chequeando proyectos registrados...[/bold]")
    projects = list_projects()
    if not projects:
        console.print("  [yellow]![/yellow] No hay proyectos en config/config.yaml")
    for p in projects:
        try:
            r = git.Repo(p.repo_path)
            console.print(f"  [green]✓[/green] {p.id} — repo válido ({r.head.commit.hexsha[:8]})")
        except git.NoSuchPathError:
            console.print(f"  [red]✗[/red] {p.id} — la ruta '{p.repo_path}' no existe")
            ok_all = False
        except git.InvalidGitRepositoryError:
            console.print(f"  [red]✗[/red] {p.id} — '{p.repo_path}' no es un repo git")
            ok_all = False

    console.print()
    if ok_all:
        console.print("[bold green]Todo en orden.[/bold green]")
    else:
        console.print("[bold yellow]Hay cosas que revisar arriba (✗).[/bold yellow]")
        raise typer.Exit(code=1)


def _configure_beacon_logging():
    """Formato prolijo para lo que loguee el paquete `beacon.*` (ej. fallas
    del watcher automático en api.py). No toca el logger raíz ni el de
    uvicorn — solo se engancha al namespace propio, así que comandos que no
    levantan el server (ask, sync, docs...) no se ven afectados en nada."""
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        fmt="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    ))
    beacon_logger = logging.getLogger("beacon")
    beacon_logger.addHandler(handler)
    beacon_logger.setLevel(logging.WARNING)
    beacon_logger.propagate = False


@app.command("serve")
def cmd_serve(host: str = "127.0.0.1", port: int = 8000):
    """Levanta la API + UI web."""
    import uvicorn
    _configure_beacon_logging()
    console.print(f"[bold]Sirviendo en[/bold] http://{host}:{port}")
    if not (Path(__file__).resolve().parents[2] / "frontend" / "dist").exists():
        console.print("[yellow]La interfaz web no está compilada[/yellow] (solo responderá la API). "
                      "Para compilarla: cd frontend, npm install, npm run build.")
    console.print("[dim]El watcher automático corre mientras este proceso esté abierto (Ctrl+C para detener).[/dim]")
    uvicorn.run("core.api:app", host=host, port=port, reload=False)


def _validate_id_or_exit(project_id: str):
    try:
        validate_project_id(project_id)
    except InvalidProjectIdError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1)


def _resolve_or_exit(project_id: str):
    try:
        return get_project(project_id)
    except ProjectNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1)


def _run_safely(fn, *, repo_path: str = ""):
    """Ejecuta fn() traduciendo los fallos más comunes a mensajes claros,
    en vez de un traceback crudo de Python. El error original siempre se
    muestra igual (en gris, debajo), por si hace falta para debug."""
    try:
        return fn()
    except ProjectBusyError as e:
        console.print(f"[yellow]{e}[/yellow]")
        console.print("[dim]Vuelve a intentarlo en unos momentos, o revisa el estado en la interfaz web.[/dim]")
        raise typer.Exit(code=1)
    except (InvalidProjectIdError, InvalidRepoUrlError, services.InvalidRequestError,
            services.CloneError, ProjectAlreadyExistsError, ProjectNotFoundError, IndexModelMismatchError) as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1)
    except git.NoSuchPathError:
        console.print(f"[red]La ruta del repo no existe:[/red] {repo_path}")
        console.print("[dim]Revisa 'repo_path' en config/config.yaml — probablemente esté mal escrita o el repo no esté clonado ahí.[/dim]")
        raise typer.Exit(code=1)
    except git.InvalidGitRepositoryError:
        console.print(f"[red]Esa carpeta existe pero no es un repositorio git:[/red] {repo_path}")
        console.print("[dim]¿Falta un 'git init' o 'git clone' ahí?[/dim]")
        raise typer.Exit(code=1)
    except IndexOutOfDateError as e:
        console.print(f"[yellow]{e}[/yellow]")
        raise typer.Exit(code=1)
    except ollama.ResponseError as e:
        console.print(f"[red]Ollama respondió con un error:[/red] {e}")
        console.print("[dim]¿El modelo está descargado? Revisa 'ollama_model'/'embedding_model' en config.yaml y corre 'ollama pull <modelo>'.[/dim]")
        raise typer.Exit(code=1)
    except Exception as e:
        msg = str(e).lower()
        if "connection" in msg or "refused" in msg or "connect" in msg:
            console.print("[red]No se pudo conectar con Ollama.[/red]")
            console.print("[dim]¿Está corriendo? Prueba 'ollama serve' en otra terminal, y confirma 'ollama_host' en config.yaml.[/dim]")
        else:
            console.print(f"[red]Algo falló inesperadamente:[/red] {e}")
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
