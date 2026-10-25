"""`beacon` command: project management, indexing, queries, API and study mode.
Messages and help are shown in the operating system language (see cli_lang).

Usage:
    beacon projects                        # list registered projects
    beacon add <id> --repo-path <path>     # register an already cloned local project
    beacon add <id> --url <repo_url>       # clone a remote repository and register it
    beacon edit <id> [--name] [--repo-path] [--auto-watch/--no-auto-watch]
    beacon remove <id> [--purge-data]      # unregister a project
    beacon status <id>                     # state of the index and the documentation
    beacon doctor                          # health check (model server, models, repos)
    beacon sync <id>                       # incremental indexing
    beacon docs <id>                       # generate the documentation
    beacon ask <id> "question"
    beacon export <id>                     # pack index and documentation into a .zip
    beacon import <file.zip>               # register a project from an exported .zip
    beacon serve                           # start the API and the web interface
    beacon study                           # study mode (see docs/en/study-mode.md)
"""

import json
import logging
import zipfile
from contextlib import nullcontext
from pathlib import Path
from typing import Optional

import git
import typer
from rich.console import Console
from rich.progress import Progress, BarColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from core.cli_lang import tr
from core.engine.backends import BackendAuthError, BackendError, BackendUnavailableError, ExternalServerError, ModelNotFoundError, create_backend
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

app = typer.Typer(
    help=tr("Beacon — plataforma de mitigación de deuda técnica", "Beacon — platform to mitigate technical debt"),
    invoke_without_command=True,
)
console = Console()


def _version() -> str:
    from importlib.metadata import PackageNotFoundError, version
    try:
        return version("beacon")
    except PackageNotFoundError:
        return tr("desarrollo", "development")


def _version_callback(show: bool):
    if show:
        console.print(f"Beacon {_version()}")
        raise typer.Exit()


@app.callback()
def main(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", "-v", callback=_version_callback, is_eager=True,
                                 help=tr("Muestra la versión y sale.", "Show the version and exit.")),
):
    """Beacon"""
    if ctx.invoked_subcommand is not None:
        return
    console.print()
    console.print(f"[bold]Beacon[/bold] {_version()} · " + tr("preguntas sobre tu código, todo en tu equipo",
                                                              "questions about your code, all on your machine"))
    console.print()
    console.print(tr("Para empezar:", "To get started:"))
    for cmd, desc in (
        ("beacon add", tr("registrar un repositorio", "register a repository")),
        ("beacon sync <id>", tr("indexarlo", "index it")),
        ("beacon ask <id> \"...?\"", tr("hacer una pregunta", "ask a question")),
        ("beacon serve", tr("abrir la interfaz web", "open the web interface")),
        ("beacon doctor", tr("revisar que todo esté bien configurado", "check that everything is set up")),
        ("beacon study", tr("participar en el estudio de evaluación", "take part in the evaluation study")),
    ):
        console.print(f"  [cyan]{cmd:<34}[/cyan] {desc}")
    console.print()
    console.print(tr("Todos los comandos: ", "All commands: ") + "[cyan]beacon --help[/cyan]. " + tr("¡Happy hacking!", "Happy hacking!"))
    console.print()


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


@app.command("projects", help=tr("Lista los proyectos registrados en config.yaml.", "List the projects registered in config.yaml."))
def cmd_list_projects():
    projects = list_projects()
    if not projects:
        console.print(tr("[yellow]No hay proyectos registrados en config/config.yaml todavía.[/yellow]",
                         "[yellow]No projects are registered in config/config.yaml yet.[/yellow]"))
        raise typer.Exit()
    table = Table(title=tr("Proyectos registrados", "Registered projects"))
    table.add_column("id"); table.add_column(tr("nombre", "name")); table.add_column("repo")
    for p in projects:
        table.add_row(p.id, p.name, p.repo_path)
    console.print(table)


@app.command("add", help=tr("Registra un proyecto nuevo: un repo local ya clonado (--repo-path), o clona uno remoto y lo registra (--url).",
                            "Register a new project: an already cloned local repo (--repo-path), or clone a remote one and register it (--url)."))
def cmd_add(
    project_id: str,
    repo_path: str = typer.Option(None, "--repo-path", help=tr("Ruta local a un repo git ya clonado.", "Local path to an already cloned git repo.")),
    url: str = typer.Option(None, "--url", help=tr("URL de un repo remoto a clonar (alternativa a --repo-path).",
                                                    "URL of a remote repo to clone (alternative to --repo-path).")),
    name: str = typer.Option(None, "--name", help=tr("Nombre visible (por defecto, el id).", "Display name (the id by default).")),
    auto_watch: bool = typer.Option(False, "--auto-watch/--no-auto-watch"),
    private: bool = typer.Option(False, "--private", help=tr("Repo privado: pide un token de acceso (no se muestra al escribirlo).",
                                                              "Private repo: asks for an access token (hidden while typing).")),
):
    token = None
    if private:
        if not url:
            console.print(tr("[red]--private solo aplica a repos remotos (--url).[/red]",
                             "[red]--private only applies to remote repos (--url).[/red]"))
            raise typer.Exit(code=1)
        token = typer.prompt(tr("Token de acceso", "Access token"), hide_input=True)
    with console.status(tr(f"Clonando {url}...", f"Cloning {url}...")) if url else nullcontext():
        entry = _run_safely(lambda: services.register_project(
            project_id, name=name, repo_path=repo_path, repo_url=url, auth_token=token, auto_watch=auto_watch,
        ), repo_path=repo_path or "")
    console.print(tr(f"[green]OK[/green] Proyecto '{entry.id}' registrado. Corre: beacon sync {entry.id}",
                     f"[green]OK[/green] Project '{entry.id}' registered. Run: beacon sync {entry.id}"))


@app.command("edit", help=tr("Edita un proyecto ya registrado (nombre, repo_path y/o auto_watch).",
                             "Edit an already registered project (name, repo_path and/or auto_watch)."))
def cmd_edit(
    project_id: str,
    name: str = typer.Option(None, "--name", help=tr("Nuevo nombre visible.", "New display name.")),
    repo_path: str = typer.Option(None, "--repo-path", help=tr("Nueva ruta local del repo.", "New local path of the repo.")),
    auto_watch: Optional[bool] = typer.Option(None, "--auto-watch/--no-auto-watch",
                                              help=tr("Activa/desactiva el watcher automático.", "Turn the automatic watcher on/off.")),
):
    if name is None and repo_path is None and auto_watch is None:
        console.print(tr("[yellow]No pasaste nada para editar — usa --name, --repo-path y/o --auto-watch/--no-auto-watch.[/yellow]",
                         "[yellow]Nothing to edit — use --name, --repo-path and/or --auto-watch/--no-auto-watch.[/yellow]"))
        raise typer.Exit(code=1)
    if repo_path is not None and not Path(repo_path).exists():
        console.print(tr(f"[red]'{repo_path}' no existe.[/red]", f"[red]'{repo_path}' does not exist.[/red]"))
        raise typer.Exit(code=1)

    changed = False
    if name is not None or repo_path is not None:
        changed = update_project(project_id, name=name, repo_path=repo_path) or changed
    if auto_watch is not None:
        changed = set_auto_watch(project_id, auto_watch) or changed

    if not changed:
        console.print(tr(f"[red]Proyecto '{project_id}' no encontrado en config.yaml.[/red]",
                         f"[red]Project '{project_id}' not found in config.yaml.[/red]"))
        raise typer.Exit(code=1)
    console.print(tr(f"[green]OK[/green] '{project_id}' actualizado.", f"[green]OK[/green] '{project_id}' updated."))


@app.command("remove", help=tr("Desregistra un proyecto de config.yaml (y opcionalmente borra sus datos indexados).",
                               "Unregister a project from config.yaml (and optionally delete its indexed data)."))
def cmd_remove(
    project_id: str,
    purge_data: bool = typer.Option(False, "--purge-data", help=tr("Además borra data/<id>/ (índice + docs) del disco.",
                                                                    "Also delete data/<id>/ (index + docs) from disk.")),
):
    _run_safely(lambda: services.unregister_project(project_id, purge_data=purge_data))
    if purge_data:
        console.print(tr(f"[green]OK[/green] '{project_id}' eliminado de config.yaml. Datos en data/ también borrados.",
                         f"[green]OK[/green] '{project_id}' removed from config.yaml. Data in data/ deleted too."))
    else:
        console.print(tr(f"[green]OK[/green] '{project_id}' eliminado de config.yaml. (los datos en data/ quedaron intactos — usa --purge-data para borrarlos).",
                         f"[green]OK[/green] '{project_id}' removed from config.yaml. (data in data/ was left untouched — use --purge-data to delete it)."))


@app.command("sync", help=tr("Pone al día el índice de un proyecto (incremental). En proyectos clonados por URL, primero trae los cambios del remoto.",
                             "Bring a project's index up to date (incremental). For projects cloned by URL, remote changes are pulled first."))
def cmd_sync(
    project_id: str,
    uncommitted: bool = typer.Option(False, "--uncommitted", help=tr("Incluye cambios sin commitear (no registra commit).",
                                                                      "Include uncommitted changes (no commit is recorded).")),
    full: bool = typer.Option(False, "--full", help=tr("Rehace el índice completo en vez de solo lo que cambió.",
                                                        "Rebuild the whole index instead of only what changed.")),
    docs: bool = typer.Option(False, "--docs", help=tr("Además actualiza la documentación (como el botón de la UI).",
                                                        "Also update the documentation (like the UI button).")),
    pull: bool = typer.Option(True, "--pull/--no-pull", help=tr("Traer cambios del remoto antes (solo proyectos clonados por URL).",
                                                                 "Pull remote changes first (only for projects cloned by URL).")),
):
    project = _resolve_or_exit(project_id)
    console.print(tr(f"[bold]Sincronizando[/bold] {project.name}...", f"[bold]Syncing[/bold] {project.name}..."))

    def _do_sync():
        with _progress_bar(tr("Procesando", "Processing")) as progress:
            idx_task = progress.add_task(tr("indexando", "indexing"), total=1, current_file="")
            doc_task = progress.add_task(tr("documentando", "documenting"), total=1, current_file="", visible=docs)
            return services.sync_project(
                project_id, docs=docs, full=full, include_uncommitted=uncommitted, pull=pull,
                on_index_progress=_progress_callback(progress, idx_task),
                on_docs_progress=_progress_callback(progress, doc_task),
            )

    result = _run_safely(_do_sync, repo_path=project.repo_path)
    if result.pulled:
        console.print(tr("[dim]Cambios del remoto traídos con git pull.[/dim]", "[dim]Remote changes pulled with git pull.[/dim]"))
    console.print(f"[green]OK[/green] {result.index}")
    if result.generated_purged:
        console.print(tr(f"[green]Purgados {result.generated_purged} chunks de código generado/vendor.[/green]",
                         f"[green]Purged {result.generated_purged} chunks of generated/vendor code.[/green]"))
    if result.docs is not None:
        console.print(tr("[green]Documentación:[/green] ", "[green]Documentation:[/green] ") + f"{result.docs}")


@app.command("docs", help=tr("Genera/actualiza documentación .md incremental para un proyecto (requiere 'sync' al día).",
                             "Generate/update incremental .md documentation for a project (requires an up-to-date 'sync')."))
def cmd_docs(
    project_id: str,
    full: bool = typer.Option(False, "--full", help=tr("Regenera toda la documentación en vez de solo lo que cambió.",
                                                        "Regenerate all the documentation instead of only what changed.")),
):
    project = _resolve_or_exit(project_id)
    console.print(tr(f"[bold]Generando documentación[/bold] para {project.name}...",
                     f"[bold]Generating documentation[/bold] for {project.name}..."))

    def _do_docs():
        with _progress_bar(tr("Documentando", "Documenting")) as progress:
            task = progress.add_task(tr("documentando", "documenting"), total=1, current_file="")
            return services.generate_docs(project_id, full=full, on_progress=_progress_callback(progress, task))

    result = _run_safely(_do_docs, repo_path=project.repo_path)
    console.print(f"[green]OK[/green] {result}")


@app.command("ask", help=tr("Consulta en lenguaje natural sobre un proyecto ya indexado.",
                            "Ask a natural-language question about an already indexed project."))
def cmd_ask(project_id: str, question: str, top_k: int = 5):
    project = _resolve_or_exit(project_id)
    engine = RAGEngine(project, load_config().ai_provider)

    if engine.collection.count() == 0:
        console.print(tr(f"[red]El índice de '{project_id}' está vacío. Corre 'beacon sync {project_id}' primero.[/red]",
                         f"[red]The index of '{project_id}' is empty. Run 'beacon sync {project_id}' first.[/red]"))
        raise typer.Exit(code=1)

    with console.status(tr("Consultando...", "Querying...")):
        result = _run_safely(lambda: engine.ask(question, top_k=top_k), repo_path=project.repo_path)

    console.print(f"\n[bold]{result.answer}[/bold]\n")
    table = Table(title=tr("Fuentes", "Sources"))
    table.add_column(tr("archivo", "file")); table.add_column(tr("tipo", "type")); table.add_column(tr("nombre", "name"))
    table.add_column(tr("origen", "origin"))
    for s in result.sources:
        origin = tr("grafo de llamadas", "call graph") if s.expanded else tr(f"semántico ({s.distance:.3f})", f"semantic ({s.distance:.3f})")
        table.add_row(f"{s.file_path}:{s.start_line}-{s.end_line}", s.chunk_type, s.name, origin)
    console.print(table)


@app.command("export", help=tr("Empaqueta el índice (ChromaDB) y la documentación de un proyecto en un .zip portable. "
                               "Útil para llevar un proyecto ya indexado a otra máquina sin reindexar en vivo.",
                               "Pack a project's index (ChromaDB) and documentation into a portable .zip. "
                               "Useful to take an already indexed project to another machine without reindexing."))
def cmd_export(project_id: str, output: str = typer.Option(None, "--output", "-o")):
    cfg = load_config()
    entry = next((p for p in cfg.projects if p.id == project_id), None)
    if entry is None:
        console.print(tr(f"[red]Proyecto '{project_id}' no encontrado en config.yaml.[/red]",
                         f"[red]Project '{project_id}' not found in config.yaml.[/red]"))
        raise typer.Exit(code=1)

    project_dir = DATA_ROOT / project_id
    if not project_dir.exists():
        console.print(tr(f"[red]No hay datos indexados para '{project_id}' todavía — corre 'beacon sync {project_id}' primero.[/red]",
                         f"[red]There is no indexed data for '{project_id}' yet — run 'beacon sync {project_id}' first.[/red]"))
        raise typer.Exit(code=1)

    output_path = Path(output) if output else Path(f"{project_id}.beacon.zip")
    manifest = {
        "id": entry.id, "name": entry.name, "repo_path": entry.repo_path,
        "source_type": entry.source_type, "repo_url": entry.repo_url,
    }
    with console.status(tr(f"Empaquetando {project_id}...", f"Packing {project_id}...")):
        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
            for file in project_dir.rglob("*"):
                if file.is_file() and file.name != LOCK_FILENAME:
                    zf.write(file, arcname=str(Path("data") / file.relative_to(project_dir)))

    size_mb = output_path.stat().st_size / 1_048_576
    console.print(tr(f"[green]OK[/green] Exportado a {output_path} ({size_mb:.1f} MB)",
                     f"[green]OK[/green] Exported to {output_path} ({size_mb:.1f} MB)"))


@app.command("import", help=tr("Registra un proyecto a partir de un .zip generado con 'beacon export'.",
                               "Register a project from a .zip created with 'beacon export'."))
def cmd_import(
    zip_path: str,
    project_id: str = typer.Option(None, "--id", help=tr("Sobreescribe el id del manifest (por si ya existe).",
                                                          "Override the manifest id (in case it already exists).")),
    repo_path: str = typer.Option(None, "--repo-path", help=tr("Sobreescribe repo_path (útil si cambió de máquina).",
                                                                "Override repo_path (useful if you changed machines).")),
):
    zpath = Path(zip_path)
    if not zpath.exists():
        console.print(tr(f"[red]No existe el archivo '{zip_path}'.[/red]", f"[red]The file '{zip_path}' does not exist.[/red]"))
        raise typer.Exit(code=1)

    with zipfile.ZipFile(zpath) as zf:
        try:
            manifest = json.loads(zf.read("manifest.json"))
        except KeyError:
            console.print(tr("[red]El .zip no tiene manifest.json — ¿fue generado con 'beacon export'?[/red]",
                             "[red]The .zip has no manifest.json — was it created with 'beacon export'?[/red]"))
            raise typer.Exit(code=1)

        target_id = project_id or manifest["id"]
        # The id comes from the .zip manifest: validate it before touching the disk.
        _validate_id_or_exit(target_id)
        target_dir = (DATA_ROOT / target_id).resolve()
        if target_dir.exists():
            console.print(tr(f"[red]Ya existe data/{target_id}/ — usa --id para elegir otro nombre, o borra esa carpeta primero.[/red]",
                             f"[red]data/{target_id}/ already exists — use --id to pick another name, or delete that folder first.[/red]"))
            raise typer.Exit(code=1)

        target_dir.mkdir(parents=True, exist_ok=True)
        with console.status(tr(f"Extrayendo a data/{target_id}/...", f"Extracting to data/{target_id}/...")):
            for member in zf.namelist():
                if member == "manifest.json" or not member.startswith("data/"):
                    continue
                dest = (target_dir / Path(member).relative_to("data")).resolve()
                if not dest.is_relative_to(target_dir):
                    continue  # entry that escapes target_dir
                dest.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as src, open(dest, "wb") as out:
                    out.write(src.read())
        bump_index_version(target_dir)  # a running server must not use previous data of this id

    final_repo_path = repo_path or manifest["repo_path"]
    try:
        add_project(ProjectEntry(
            id=target_id, name=manifest["name"], repo_path=final_repo_path,
            source_type=manifest.get("source_type", "local"), repo_url=manifest.get("repo_url"),
        ))
    except ProjectAlreadyExistsError as e:
        console.print(f"[red]{e}[/red] " + tr(f"[dim](los datos ya se copiaron a data/{target_id}/, pero no se registró en config.yaml)[/dim]",
                                              f"[dim](the data was already copied to data/{target_id}/, but it was not registered in config.yaml)[/dim]"))
        raise typer.Exit(code=1)

    console.print(tr(f"[green]OK[/green] Importado como '{target_id}'. Prueba: beacon ask {target_id} \"...\"",
                     f"[green]OK[/green] Imported as '{target_id}'. Try: beacon ask {target_id} \"...\""))
    if not Path(final_repo_path).exists():
        console.print(tr(
            f"[yellow]![/yellow] 'repo_path' ({final_repo_path}) no existe en esta máquina — "
            "las consultas sobre lo ya indexado funcionan igual, pero 'sync'/'docs' incrementales "
            "van a fallar hasta que ajustes la ruta en config.yaml (o reimportes con --repo-path).",
            f"[yellow]![/yellow] 'repo_path' ({final_repo_path}) does not exist on this machine — "
            "queries over what is already indexed work the same, but incremental 'sync'/'docs' "
            "will fail until you fix the path in config.yaml (or re-import with --repo-path).",
        ))


@app.command("status", help=tr("Muestra el estado del índice y la documentación de un proyecto.",
                               "Show the state of a project's index and documentation."))
def cmd_status(project_id: str):
    project = _resolve_or_exit(project_id)
    ai = load_config().ai_provider

    def _do_status():
        indexer = CodebaseIndexer(project, ai)
        generator = DocGenerator(project, ai)
        return indexer.stats(), generator.stats()

    index_stats, doc_stats = _run_safely(_do_status, repo_path=project.repo_path)

    never = tr("nunca", "never")
    yes, no = tr("sí", "yes"), tr("no", "no")
    current_commit = index_stats["commit_actual"][:8]
    last_indexed = (index_stats["ultimo_commit_indexado"] or never)[:8] if index_stats["ultimo_commit_indexado"] else never
    last_documented = (doc_stats["ultimo_commit_documentado"] or never)[:8] if doc_stats["ultimo_commit_documentado"] else never

    table = Table(title=tr(f"Estado — {project.name}", f"Status — {project.name}"))
    table.add_column("")
    table.add_column(tr("valor", "value"))
    table.add_row(tr("Chunks indexados", "Indexed chunks"), str(index_stats["total_chunks"]))
    table.add_row(tr("Commit actual del repo", "Current repo commit"), current_commit)
    table.add_row(tr("Último commit indexado", "Last indexed commit"), last_indexed)
    table.add_row(tr("Índice al día", "Index up to date"),
                  f"[green]{yes}[/green]" if last_indexed == current_commit else f"[yellow]{no} — " + tr("corre 'sync'", "run 'sync'") + "[/yellow]")
    table.add_row(tr("Último commit documentado", "Last documented commit"), last_documented)
    table.add_row(tr("Docs al día", "Docs up to date"),
                  f"[green]{yes}[/green]" if last_documented == current_commit else f"[yellow]{no} — " + tr("corre 'docs'", "run 'docs'") + "[/yellow]")
    console.print(table)


@app.command("doctor", help=tr("Chequeo de salud: Ollama, modelos, y proyectos registrados.",
                               "Health check: Ollama, models, and registered projects."))
def cmd_doctor():
    cfg = load_config()
    ok_all = True

    ai = cfg.ai_provider
    name = ai.provider.capitalize()
    console.print(tr(f"[bold]Chequeando {name}...[/bold]", f"[bold]Checking {name}...[/bold]"))
    try:
        backend = create_backend(ai.provider, ai.host)
        installed = set(backend.list_models())
        console.print(tr(f"  [green]✓[/green] Conectado en {ai.host}", f"  [green]✓[/green] Connected at {ai.host}"))

        for label, model in [("embedding_model", ai.embedding_model), ("llm_model", ai.llm_model)]:
            match = any(model in m for m in installed)
            hint = backend.install_hint(model)
            if match:
                console.print(tr(f"  [green]✓[/green] {label} ({model}) está disponible", f"  [green]✓[/green] {label} ({model}) is available"))
            else:
                console.print(tr(f"  [red]✗[/red] {label} ({model}) NO está disponible — corre: {hint}",
                                 f"  [red]✗[/red] {label} ({model}) is NOT available — run: {hint}"))
                ok_all = False
    except Exception as e:
        console.print(tr(f"  [red]✗[/red] No se pudo conectar a {name} en {ai.host}: {e}",
                         f"  [red]✗[/red] Could not connect to {name} at {ai.host}: {e}"))
        start = backend.start_hint() if "backend" in locals() else ""
        if start:
            console.print(tr(f"  [dim]¿Corriste '{start}'?[/dim]", f"  [dim]Did you run '{start}'?[/dim]"))
        ok_all = False

    console.print(tr("\n[bold]Chequeando proyectos registrados...[/bold]", "\n[bold]Checking registered projects...[/bold]"))
    projects = list_projects()
    if not projects:
        console.print(tr("  [yellow]![/yellow] No hay proyectos en config/config.yaml", "  [yellow]![/yellow] No projects in config/config.yaml"))
    for p in projects:
        try:
            r = git.Repo(p.repo_path)
            console.print(tr(f"  [green]✓[/green] {p.id} — repo válido ({r.head.commit.hexsha[:8]})",
                             f"  [green]✓[/green] {p.id} — valid repo ({r.head.commit.hexsha[:8]})"))
        except git.NoSuchPathError:
            console.print(tr(f"  [red]✗[/red] {p.id} — la ruta '{p.repo_path}' no existe",
                             f"  [red]✗[/red] {p.id} — the path '{p.repo_path}' does not exist"))
            ok_all = False
        except git.InvalidGitRepositoryError:
            console.print(tr(f"  [red]✗[/red] {p.id} — '{p.repo_path}' no es un repo git",
                             f"  [red]✗[/red] {p.id} — '{p.repo_path}' is not a git repo"))
            ok_all = False

    console.print()
    if ok_all:
        console.print(tr("[bold green]Todo en orden.[/bold green]", "[bold green]All good.[/bold green]"))
    else:
        console.print(tr("[bold yellow]Hay cosas que revisar arriba (✗).[/bold yellow]",
                         "[bold yellow]Some things need attention above (✗).[/bold yellow]"))
        raise typer.Exit(code=1)


def _configure_beacon_logging():
    """Log format for the `beacon.*` package (e.g. automatic watcher failures in api.py). It does not touch
    the root or uvicorn loggers, so commands that do not start the server are unaffected.
    """
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        fmt="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    ))
    beacon_logger = logging.getLogger("beacon")
    beacon_logger.addHandler(handler)
    beacon_logger.setLevel(logging.WARNING)
    beacon_logger.propagate = False


@app.command("serve", help=tr("Levanta la API + UI web.", "Start the API + web UI."))
def cmd_serve(host: str = "127.0.0.1", port: int = 8000):
    import uvicorn
    _configure_beacon_logging()
    console.print(tr(f"[bold]Sirviendo en[/bold] http://{host}:{port}", f"[bold]Serving at[/bold] http://{host}:{port}"))
    if not (Path(__file__).resolve().parents[2] / "frontend" / "dist").exists():
        console.print(tr("[yellow]La interfaz web no está compilada[/yellow] (solo responderá la API). "
                         "Para compilarla: cd frontend, npm install, npm run build.",
                         "[yellow]The web interface is not built[/yellow] (only the API will respond). "
                         "To build it: cd frontend, npm install, npm run build."))
    console.print(tr("[dim]El watcher automático corre mientras este proceso esté abierto (Ctrl+C para detener).[/dim]",
                     "[dim]The automatic watcher runs while this process is open (Ctrl+C to stop).[/dim]"))
    uvicorn.run("core.api:app", host=host, port=port, reload=False)


@app.command("space", help=tr("Muestra cuánto espacio en disco usa cada proyecto (clon, índice y documentación).",
                              "Show how much disk space each project uses (clone, index and documentation)."))
def cmd_space():
    from core import storage
    usage = storage.list_usage()
    if not usage:
        console.print(tr("[yellow]No hay proyectos registrados.[/yellow]", "[yellow]No projects are registered.[/yellow]"))
        raise typer.Exit()
    table = Table(title=tr("Espacio en disco por proyecto", "Disk space per project"))
    table.add_column("id")
    for col in (tr("clon", "clone"), tr("índice", "index"), tr("documentación", "documentation"), "total"):
        table.add_column(col, justify="right")
    for u in usage:
        clone = storage.human_size(u.clone_bytes) if u.clone_path else tr("(carpeta tuya)", "(your folder)")
        table.add_row(u.id, clone, storage.human_size(u.index_bytes), storage.human_size(u.docs_bytes), storage.human_size(u.total_bytes))
    console.print(table)
    console.print(tr("[dim]Para liberar espacio: beacon clean <id> --clone --index --docs (el proyecto sigue registrado; "
                     "lo borrado se vuelve a crear con 'beacon sync').[/dim]",
                     "[dim]To free space: beacon clean <id> --clone --index --docs (the project stays registered; "
                     "what is deleted is recreated by 'beacon sync').[/dim]"))


@app.command("clean", help=tr("Libera espacio de un proyecto sin quitarlo: borra su clon, su índice y/o su documentación.",
                              "Free space for a project without removing it: delete its clone, index and/or documentation."))
def cmd_clean(
    project_id: str,
    clone: bool = typer.Option(False, "--clone", help=tr("Borra el clon del repositorio (solo si Beacon lo clonó por URL).",
                                                          "Delete the repository clone (only if Beacon cloned it by URL).")),
    index: bool = typer.Option(False, "--index", help=tr("Borra el índice vectorial.", "Delete the vector index.")),
    docs: bool = typer.Option(False, "--docs", help=tr("Borra la documentación generada.", "Delete the generated documentation.")),
    yes: bool = typer.Option(False, "--yes", "-y", help=tr("No pedir confirmación.", "Do not ask for confirmation.")),
):
    from core import storage
    if not (clone or index or docs):
        console.print(tr("[yellow]Indica qué borrar: --clone, --index y/o --docs (mira 'beacon space').[/yellow]",
                         "[yellow]Say what to delete: --clone, --index and/or --docs (see 'beacon space').[/yellow]"))
        raise typer.Exit(code=1)
    _resolve_or_exit(project_id)
    if not yes:
        console.print(tr(f"Se va a borrar de '{project_id}':", f"This will delete from '{project_id}':"))
        if clone:
            console.print(tr("  • el clon del repositorio: se vuelve a descargar solo en el siguiente 'beacon sync' (necesita conexión). "
                             "Si el proyecto es una carpeta tuya, esa carpeta no se toca.",
                             "  • the repository clone: it is downloaded again on the next 'beacon sync' (needs a connection). "
                             "If the project is your own folder, that folder is not touched."))
        if index:
            console.print(tr("  • el índice: hasta el siguiente 'beacon sync' no se podrán hacer preguntas, y reconstruirlo puede tardar.",
                             "  • the index: until the next 'beacon sync' questions cannot be asked, and rebuilding it can take a while."))
        if docs:
            console.print(tr("  • la documentación generada: se regenera con 'beacon docs'.",
                             "  • the generated documentation: it is regenerated with 'beacon docs'."))
        console.print(tr("El proyecto sigue registrado.", "The project stays registered."))
        if not typer.confirm(tr("¿Continuar?", "Continue?"), default=False):
            raise typer.Exit()
    freed = _run_safely(lambda: storage.clean_project(project_id, clone=clone, index=index, docs=docs))
    console.print(tr(f"[green]OK[/green] Liberados {storage.human_size(freed)}.", f"[green]OK[/green] Freed {storage.human_size(freed)}."))


def _remove_study_data():
    import shutil
    from core.study.api import study_project_ids
    for project_id in study_project_ids() + ["estudio"]:
        shutil.rmtree(DATA_ROOT / project_id, ignore_errors=True)


@app.command("study", help=tr("Modo estudio: una sesión guiada, con tu aprobación en cada paso, que genera dos archivos locales que puedes "
                              "revisar antes de subirlos. No toca tu configuración ni tus proyectos.",
                              "Study mode: a guided session, with your approval at every step, that generates two local files you can "
                              "review before uploading them. It does not touch your configuration or projects."))
def cmd_study(
    profile: str = typer.Option("open", "--profile", help=tr("'open' (elige su propio repositorio) o 'pilot' (tareas asignadas).",
                                                              "'open' (you pick your own repository) or 'pilot' (assigned tasks).")),
    code: Optional[str] = typer.Option(None, "--code", help=tr("Código de participante (obligatorio en el perfil pilot).",
                                                                "Participant code (required for the pilot profile).")),
    order: int = typer.Option(1, "--order", help=tr("Orden de las condiciones del piloto (1 o 2).", "Order of the pilot conditions (1 or 2).")),
    language: str = typer.Option("es", "--language", help=tr("Idioma de las respuestas: es o en.", "Language of the answers: es or en.")),
    port: int = typer.Option(8000, "--port", help=tr("Puerto local.", "Local port.")),
    new: bool = typer.Option(False, "--new", help=tr("Descarta una sesión anterior sin terminar y empieza de cero.",
                                                      "Discard an unfinished previous session and start over.")),
    no_browser: bool = typer.Option(False, "--no-browser", help=tr("No abrir el navegador automáticamente.", "Do not open the browser automatically.")),
):
    import os
    import shutil
    import subprocess
    import sys
    import threading
    import webbrowser

    import yaml

    from core.study import hardware
    from core.study.session import StudyError, StudySession

    study_dir = DATA_ROOT / "_study"
    study_dir.mkdir(parents=True, exist_ok=True)
    session = StudySession(study_dir / "sesion.json")
    if new:
        session.discard()
        _remove_study_data()
    if not session.started:
        try:
            session.start(profile={"open": "abierto", "pilot": "piloto"}.get(profile, profile), code=code, order=order,
                          language=language, beacon_version=_version(), beacon_commit=hardware.beacon_commit())
        except StudyError as e:
            console.print(f"[red]{e}[/red]")
            raise typer.Exit(code=1)
    # Study-specific configuration: it uses the Ollama and the models already installed.
    try:
        ai = load_config().ai_provider
        ai_cfg = {"provider": ai.provider, "host": ai.host, "embedding_model": ai.embedding_model,
                  "llm_model": ai.llm_model}
    except FileNotFoundError:
        ai_cfg = {"provider": "ollama", "host": "http://localhost:11434", "embedding_model": "qwen3-embedding:0.6b",
                  "llm_model": "llama3:8b"}
    study_config = study_dir / "config.yaml"
    projects = []
    if study_config.exists() and session.started:  # resumed session: the prepared repository stays registered
        try:
            projects = (yaml.safe_load(study_config.read_text(encoding="utf-8")) or {}).get("projects") or []
        except yaml.YAMLError:
            projects = []
    study_config.write_text(yaml.safe_dump({"ai_provider": ai_cfg, "projects": projects}, allow_unicode=True), encoding="utf-8")
    env = dict(os.environ, BEACON_STUDY="1", DEUDA_TECNICA_CONFIG=str(study_config))
    url = f"http://127.0.0.1:{port}/study"
    console.print(tr(f"[bold]Modo estudio[/bold] en {url}", f"[bold]Study mode[/bold] at {url}"))
    console.print(tr("[dim]Todo queda en este equipo hasta que subas los archivos que decidas. Ctrl+C para salir.[/dim]",
                     "[dim]Everything stays on this machine until you upload the files you choose. Ctrl+C to quit.[/dim]"))
    if not no_browser:
        threading.Timer(4.0, lambda: webbrowser.open(url)).start()
    try:
        subprocess.run([sys.executable, "-m", "uvicorn", "core.api:app", "--host", "127.0.0.1", "--port", str(port),
                        "--timeout-graceful-shutdown", "3"], env=env)
    except KeyboardInterrupt:
        pass
    finally:
        if not StudySession(study_dir / "sesion.json").started:
            _remove_study_data()
            console.print(tr("[dim]Sesión descartada: la próxima vez el estudio parte desde cero.[/dim]",
                             "[dim]Session discarded: next time the study starts from scratch.[/dim]"))
            return
        # On exit, ask whether to discard the session and the cloned repository; files already generated are
        # left alone. With no answer (Ctrl+C again, closed input) the session is kept.
        try:
            discard = typer.confirm(tr("¿Descartar la sesión del estudio? Si dices que no, la próxima vez continúa donde quedó",
                                       "Discard the study session? If you say no, next time it continues where it left off"), default=True)
        except (typer.Abort, EOFError):
            discard = False
        if discard:
            session.discard()
            _remove_study_data()
            console.print(tr("[dim]Sesión descartada: la próxima vez el estudio parte desde cero.[/dim]",
                             "[dim]Session discarded: next time the study starts from scratch.[/dim]"))
        else:
            console.print(tr("[dim]Sesión conservada: la próxima vez continúa donde quedó (o usa --new para empezar de cero).[/dim]",
                             "[dim]Session kept: next time it continues where it left off (or use --new to start over).[/dim]"))


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
    """Runs fn() translating the most common failures into clear messages instead of a raw traceback; the original error is still shown (dimmed, below)."""
    try:
        return fn()
    except ProjectBusyError as e:
        console.print(f"[yellow]{e}[/yellow]")
        console.print(tr("[dim]Vuelve a intentarlo en unos momentos, o revisa el estado en la interfaz web.[/dim]",
                         "[dim]Try again in a moment, or check the status in the web interface.[/dim]"))
        raise typer.Exit(code=1)
    except (InvalidProjectIdError, InvalidRepoUrlError, services.InvalidRequestError,
            services.CloneError, ProjectAlreadyExistsError, ProjectNotFoundError, IndexModelMismatchError) as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1)
    except git.NoSuchPathError:
        console.print(tr("[red]La ruta del repo no existe:[/red] ", "[red]The repo path does not exist:[/red] ") + repo_path)
        console.print(tr("[dim]Revisa 'repo_path' en config/config.yaml — probablemente esté mal escrita o el repo no esté clonado ahí.[/dim]",
                         "[dim]Check 'repo_path' in config/config.yaml — it is probably misspelled or the repo is not cloned there.[/dim]"))
        raise typer.Exit(code=1)
    except git.InvalidGitRepositoryError:
        console.print(tr("[red]Esa carpeta existe pero no es un repositorio git:[/red] ", "[red]That folder exists but is not a git repository:[/red] ") + repo_path)
        console.print(tr("[dim]¿Falta un 'git init' o 'git clone' ahí?[/dim]", "[dim]Is a 'git init' or 'git clone' missing there?[/dim]"))
        raise typer.Exit(code=1)
    except IndexOutOfDateError as e:
        console.print(f"[yellow]{e}[/yellow]")
        raise typer.Exit(code=1)
    except ModelNotFoundError as e:
        console.print(tr("[red]El servidor de modelos no tiene el modelo pedido:[/red] ", "[red]The model server does not have the requested model:[/red] ") + f"{e.model}")
        console.print(tr("[dim]Revisa 'llm_model' y 'embedding_model' en config.yaml y descarga el modelo que falte.[/dim]",
                         "[dim]Check 'llm_model' and 'embedding_model' in config.yaml and download the missing model.[/dim]"))
        raise typer.Exit(code=1)
    except (ExternalServerError, BackendAuthError) as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1)
    except BackendUnavailableError as e:
        console.print(tr("[red]No se pudo conectar con el servidor de modelos.[/red]", "[red]Could not connect to the model server.[/red]"))
        console.print(f"[dim]{e}[/dim]")
        console.print(tr("[dim]¿Está corriendo? Confirma 'provider' y 'host' en config.yaml (beacon doctor ayuda a diagnosticarlo).[/dim]",
                         "[dim]Is it running? Confirm 'provider' and 'host' in config.yaml (beacon doctor helps diagnose it).[/dim]"))
        raise typer.Exit(code=1)
    except BackendError as e:
        console.print(tr("[red]El servidor de modelos respondió con un error:[/red] ", "[red]The model server responded with an error:[/red] ") + f"{e}")
        raise typer.Exit(code=1)
    except Exception as e:
        console.print(tr("[red]Algo falló inesperadamente:[/red] ", "[red]Something failed unexpectedly:[/red] ") + f"{e}")
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
