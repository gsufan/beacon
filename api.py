"""
API REST — Pilar 5 de la propuesta (Entregable: API REST / Interfaz de Consulta).

Expone el motor RAG (rag_engine.py) vía HTTP para que el desarrollador pueda
consultar en lenguaje natural sobre la arquitectura, flujo de datos o
soluciones a errores, sin salir del entorno local.
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from rag_engine import RAGEngine

app = FastAPI(
    title="Plataforma Local de Mitigación de Deuda Técnica — API",
    description="Motor RAG políglota para consulta semántica de repositorios de código.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # entorno local/dev; restringir en un despliegue real
    allow_methods=["*"],
    allow_headers=["*"],
)

# El engine se instancia una sola vez (mantiene la conexión a ChromaDB abierta).
engine = RAGEngine()


class QueryRequest(BaseModel):
    question: str = Field(..., min_length=3, description="Pregunta en lenguaje natural sobre el código")
    top_k: int = Field(default=5, ge=1, le=20, description="Cantidad de fragmentos a recuperar")


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


@app.get("/health")
def health():
    return {"status": "ok", "total_chunks_indexados": engine.collection.count()}


@app.post("/query", response_model=QueryResponse)
def query(req: QueryRequest):
    if engine.collection.count() == 0:
        raise HTTPException(
            status_code=409,
            detail="El índice está vacío. Corre indexer.py sobre tu repositorio primero.",
        )
    try:
        result = engine.ask(req.question, top_k=req.top_k)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al consultar el motor RAG: {e}")
    return result.to_dict()


@app.get("/", response_class=HTMLResponse)
def ui():
    """UI mínima de consulta en una sola página, sin dependencias externas."""
    return """
<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<title>Consulta de Repositorio — RAG Local</title>
<style>
  body { font-family: -apple-system, sans-serif; max-width: 800px; margin: 40px auto; padding: 0 20px; background: #0f1117; color: #e2e2e2; }
  h1 { font-size: 1.4rem; color: #f5f5f5; }
  textarea { width: 100%; box-sizing: border-box; padding: 12px; font-size: 1rem; border-radius: 8px; border: 1px solid #333; background: #1a1d27; color: #eee; resize: vertical; }
  button { margin-top: 10px; padding: 10px 20px; background: #4f7cff; color: white; border: none; border-radius: 6px; cursor: pointer; font-size: 1rem; }
  button:disabled { background: #555; cursor: not-allowed; }
  #answer { margin-top: 20px; white-space: pre-wrap; line-height: 1.5; background: #1a1d27; padding: 16px; border-radius: 8px; min-height: 40px; }
  .source { font-size: 0.85rem; color: #9aa; margin-top: 4px; }
  .status { font-size: 0.85rem; color: #888; margin-top: 8px; }
</style>
</head>
<body>
  <h1>🔍 Consulta tu Repositorio (RAG 100% Local)</h1>
  <textarea id="question" rows="3" placeholder="Ej: ¿Cómo funciona la indexación incremental? ¿Qué hace la clase Indexer?"></textarea>
  <br>
  <button id="askBtn" onclick="ask()">Preguntar</button>
  <div class="status" id="status"></div>
  <div id="answer"></div>

  <script>
    async function ask() {
      const question = document.getElementById('question').value.trim();
      if (!question) return;
      const btn = document.getElementById('askBtn');
      const status = document.getElementById('status');
      const answerEl = document.getElementById('answer');
      btn.disabled = true;
      status.textContent = 'Consultando el índice local...';
      answerEl.textContent = '';
      try {
        const res = await fetch('/query', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({question, top_k: 5})
        });
        if (!res.ok) {
          const err = await res.json();
          answerEl.textContent = 'Error: ' + (err.detail || res.statusText);
          status.textContent = '';
          btn.disabled = false;
          return;
        }
        const data = await res.json();
        let html = data.answer + '\\n\\n';
        if (data.sources.length) {
          html += '--- Fuentes ---\\n';
          data.sources.forEach(s => {
            html += `${s.file_path} (líneas ${s.start_line}-${s.end_line}) — ${s.chunk_type} '${s.name}'\\n`;
          });
        }
        answerEl.textContent = html;
        status.textContent = '';
      } catch (e) {
        answerEl.textContent = 'Error de conexión: ' + e.message;
        status.textContent = '';
      }
      btn.disabled = false;
    }
  </script>
</body>
</html>
"""
