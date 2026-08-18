"""
Punto de entrada único. Antes: 5 scripts sueltos, cada uno con su propia
consola muda. Ahora: un comando, con progreso real y mensajes claros.

Uso:
    beacon projects                        # lista proyectos registrados
    beacon doctor                          # chequeo de salud (Ollama, modelos, repos)
    beacon status <id>                     # estado del índice/documentación de un proyecto
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
from pathlib import Path

import git
import ollama
import typer
from rich.console import Console
from rich.progress import Progress, BarColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from core.config import ProjectAlreadyExistsError, ProjectEntry, add_project, load_config
from core.projects import DATA_ROOT, list_projects, get_project, ProjectNotFoundError
from core.engine.indexer import CodebaseIndexer
from core.engine.doc_generator import DocGenerator
from core.engine.rag_engine import RAGEngine
from core.engine.git_watcher import _is_generated_or_vendor

app = typer.Typer(help="Beacon — plataforma de mitigación de deuda técnica")
console = Console()


def _progress_bar(description: str):
    return Progress(
        TextColumn(description), BarColumn(), TextColumn("{task.completed}/{task.total}"),
        TextColumn("· {task.fields[current_file]}"), TimeElapsedColumn(), console=console,
    )


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


@app.command("sync")
def cmd_sync(project_id: str, purge_generated: bool = True, uncommitted: bool = False):
    """Indexa un proyecto (incremental) y purga código generado/vendor."""
    project = _resolve_or_exit(project_id)
    cfg = load_config()

    console.print(f"[bold]Sincronizando[/bold] {project.name}...")

    def _do_sync():
        indexer = CodebaseIndexer(project, cfg.ai_provider)
        with _progress_bar("Indexando") as progress:
            task = progress.add_task("indexando", total=1, current_file="")
            def on_progress(i, total, fp):
                if progress.tasks[task].total != total:
                    progress.update(task, total=total)
                progress.update(task, completed=i, current_file=fp)
            result = indexer.sync(include_uncommitted=uncommitted, on_progress=on_progress)
        return indexer, result

    indexer, result = _run_safely(_do_sync, repo_path=project.repo_path)
    console.print(f"[green]OK[/green] {result}")

    if purge_generated:
        data = indexer.collection.get(include=["metadatas"])
        to_delete = [cid for cid, m in zip(data["ids"], data["metadatas"])
                     if _is_generated_or_vendor(m.get("file_path", ""))]
        if to_delete:
            indexer.collection.delete(ids=to_delete)
            console.print(f"[green]Purgados {len(to_delete)} chunks de código generado/vendor.[/green]")


@app.command("docs")
def cmd_docs(project_id: str):
    """Genera/actualiza documentación .md incremental para un proyecto."""
    project = _resolve_or_exit(project_id)
    cfg = load_config()

    console.print(f"[bold]Generando documentación[/bold] para {project.name}...")

    def _do_docs():
        generator = DocGenerator(project, cfg.ai_provider)
        with _progress_bar("Documentando") as progress:
            task = progress.add_task("documentando", total=1, current_file="")
            def on_progress(i, total, fp):
                if progress.tasks[task].total != total:
                    progress.update(task, total=total)
                progress.update(task, completed=i, current_file=fp)
            return generator.sync(on_progress=on_progress)

    result = _run_safely(_do_docs, repo_path=project.repo_path)
    console.print(f"[green]OK[/green] {result}")


@app.command("ask")
def cmd_ask(project_id: str, question: str, top_k: int = 5):
    """Consulta en lenguaje natural sobre un proyecto ya indexado."""
    project = _resolve_or_exit(project_id)
    cfg = load_config()
    engine = RAGEngine(project, cfg.ai_provider)

    if engine.collection.count() == 0:
        console.print(f"[red]El índice de '{project_id}' está vacío. Corre 'deuda-tecnica sync {project_id}' primero.[/red]")
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
                if file.is_file():
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

    final_repo_path = repo_path or manifest["repo_path"]
    try:
        add_project(ProjectEntry(
            id=target_id, name=manifest["name"], repo_path=final_repo_path,
            source_type=manifest.get("source_type", "local"), repo_url=manifest.get("repo_url"),
        ))
    except ProjectAlreadyExistsError as e:
        console.print(f"[red]{e}[/red] [dim](los datos ya se copiaron a data/{target_id}/, pero no se registró en config.yaml)[/dim]")
        raise typer.Exit(code=1)

    console.print(f"[green]OK[/green] Importado como '{target_id}'. Probá: beacon ask {target_id} \"...\"")
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
    uvicorn.run("core.api:app", host=host, port=port, reload=False)


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
    except git.NoSuchPathError:
        console.print(f"[red]La ruta del repo no existe:[/red] {repo_path}")
        console.print("[dim]Revisa 'repo_path' en config/config.yaml — probablemente esté mal escrita o el repo no esté clonado ahí.[/dim]")
        raise typer.Exit(code=1)
    except git.InvalidGitRepositoryError:
        console.print(f"[red]Esa carpeta existe pero no es un repositorio git:[/red] {repo_path}")
        console.print("[dim]¿Falta un 'git init' o 'git clone' ahí?[/dim]")
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
