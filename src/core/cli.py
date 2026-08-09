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
    beacon serve                           # levanta la API + UI
"""

import git
import ollama
import typer
from rich.console import Console
from rich.progress import Progress, BarColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from core.config import load_config
from core.projects import list_projects, get_project, ProjectNotFoundError
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


@app.command("serve")
def cmd_serve(host: str = "127.0.0.1", port: int = 8000):
    """Levanta la API + UI web."""
    import uvicorn
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
