"""API REST + UI. Ahora sirve MÚLTIPLES proyectos, elegidos por project_id."""

from functools import lru_cache

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from core.config import load_config
from core.projects import list_projects, get_project, ProjectNotFoundError
from core.engine.rag_engine import RAGEngine

app = FastAPI(title="Beacon API", version="0.2.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@lru_cache(maxsize=None)
def _get_engine(project_id: str) -> RAGEngine:
    """Una instancia de RAGEngine por proyecto, reutilizada entre requests."""
    project = get_project(project_id)
    cfg = load_config()
    return RAGEngine(project, cfg.ai_provider)


class QueryRequest(BaseModel):
    question: str = Field(..., min_length=3)
    top_k: int = Field(default=5, ge=1, le=20)


class SourceItem(BaseModel):
    file_path: str
    chunk_type: str
    name: str
    start_line: int
    end_line: int
    distance: float
    expanded: bool = False


class QueryResponse(BaseModel):
    answer: str
    sources: list[SourceItem]


@app.get("/projects")
def get_projects():
    return [{"id": p.id, "name": p.name} for p in list_projects()]


@app.get("/projects/{project_id}/health")
def project_health(project_id: str):
    try:
        engine = _get_engine(project_id)
    except ProjectNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"status": "ok", "total_chunks_indexados": engine.collection.count()}


@app.post("/projects/{project_id}/query", response_model=QueryResponse)
def query(project_id: str, req: QueryRequest):
    try:
        engine = _get_engine(project_id)
    except ProjectNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    if engine.collection.count() == 0:
        raise HTTPException(status_code=409, detail=f"El índice de '{project_id}' está vacío. Corre 'deuda-tecnica sync {project_id}' primero.")
    try:
        result = engine.ask(req.question, top_k=req.top_k)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error en el motor RAG: {e}")
    return result.to_dict()


@app.get("/", response_class=HTMLResponse)
def ui():
    return """<!DOCTYPE html><html lang="es"><head><meta charset="UTF-8">
<title>Beacon</title>
<style>
body{font-family:-apple-system,sans-serif;max-width:800px;margin:40px auto;padding:0 20px;background:#0f1117;color:#e2e2e2}
select,textarea{width:100%;box-sizing:border-box;padding:10px;font-size:1rem;border-radius:8px;border:1px solid #333;background:#1a1d27;color:#eee;margin-bottom:10px}
button{padding:10px 20px;background:#4f7cff;color:#fff;border:none;border-radius:6px;cursor:pointer}
#answer{margin-top:20px;white-space:pre-wrap;background:#1a1d27;padding:16px;border-radius:8px}
</style></head><body>
<h1>Beacon</h1>
<select id="project"></select>
<textarea id="question" rows="3" placeholder="Tu pregunta..."></textarea>
<button onclick="ask()">Preguntar</button>
<div id="answer"></div>
<script>
async function loadProjects(){
  const r = await fetch('/projects'); const projects = await r.json();
  const sel = document.getElementById('project');
  sel.innerHTML = projects.map(p => `<option value="${p.id}">${p.name}</option>`).join('');
}
async function ask(){
  const project = document.getElementById('project').value;
  const question = document.getElementById('question').value.trim();
  if(!question) return;
  const answerEl = document.getElementById('answer');
  answerEl.textContent = 'Consultando...';
  const res = await fetch(`/projects/${project}/query`, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({question, top_k:5})});
  const data = await res.json();
  if(!res.ok){ answerEl.textContent = 'Error: ' + (data.detail||res.statusText); return; }
  let html = data.answer + '\\n\\n--- Fuentes ---\\n';
  data.sources.forEach(s => html += `${s.file_path} (${s.start_line}-${s.end_line}) ${s.expanded?'[grafo]':''}\\n`);
  answerEl.textContent = html;
}
loadProjects();
</script></body></html>"""
