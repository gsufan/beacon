"""Assistance that uses the local language model on top of what Beacon already knows: a test plan from the impact of
a change, a handover summary of someone's work, related issues (a suggestion, never a block) and the question that
turns an issue into an implementation plan."""

import math

from core.engine.embeddings import embed_documents
from core.engine.llm import chat

LANG = {"es": "Responde en español neutro.", "en": "Answer in English."}
RELATED_MIN = 0.58  # issue to issue, both embedded as documents (measured on psf/requests)


def _ask_model(engine, system: str, user: str, max_tokens: int = 900) -> str:
    cfg = engine.ai_config
    return chat(engine.backend, cfg.llm_model, system, user, cfg.llm_num_ctx, max_tokens=max_tokens).content.strip()


def _code_of(engine, name: str, limit_chars: int = 2500) -> str:
    try:
        chunks = engine.retriever.chunks_named(name)
    except Exception:  # noqa: BLE001
        return ""
    return "\n\n".join(f"--- {c.file_path} ({c.start_line}-{c.end_line})\n{c.code}" for c in chunks[:2])[:limit_chars]


def qa_plan(engine, impact: dict, issue: dict | None, language: str = "es") -> str:
    """Test cases (Given / When / Then) for a change, grounded on the affected code and the existing tests."""
    parts = []
    for t in impact["targets"]:
        parts.append(f"## Función a cambiar: {t['target']}\n{_code_of(engine, t['target'])}")
        if t["callers"]:
            parts.append("Llamada por: " + ", ".join(f"{c['name']} ({c['file_path']})" for c in t["callers"][:10]))
        if t["indirect"]:
            parts.append("Indirectamente por: " + ", ".join(f"{c['name']} ({c['file_path']})" for c in t["indirect"][:10]))
        parts.append("Pruebas existentes que la ejercitan: " + (", ".join(f"{c['name']} ({c['file_path']})" for c in t["tests"][:10]) or "ninguna"))
    task = f"Tarea: {issue.get('ref', '')} {issue.get('title', '')}\n{(issue.get('body') or '')[:1200]}\n\n" if issue else ""
    system = ("Eres un analista de QA. A partir del código afectado por un cambio, quiénes lo llaman y las pruebas que ya existen, "
              "propón casos de prueba concretos en formato Dado / Cuando / Entonces. Separa: (1) pruebas de regresión para los "
              "llamadores, (2) casos nuevos para el cambio, (3) huecos: lo que hoy no tiene pruebas. Nombra solo funciones y archivos "
              "que aparecen en el contexto. Sé breve. " + LANG.get(language, LANG["es"]))
    return _ask_model(engine, system, task + "\n\n".join(parts), max_tokens=1000)


def handover_summary(engine, person: str, issues: list, commits: list, local_commits: list, language: str = "es") -> str:
    """What a person leaves in progress, for whoever picks up their work (vacation, rotation, leaving)."""
    lines = [f"Persona: {person}", "Issues abiertas asignadas:"]
    lines += [f"- {i.get('ref')} {i.get('title')} [{i.get('state', '')}]" for i in issues[:20]] or ["- ninguna"]
    lines.append("Commits recientes en el repositorio remoto:")
    lines += [f"- {c.get('date', '')[:10]} {c.get('message')} ({', '.join('#' + str(n) for n in c.get('issues', []))})" for c in commits[:25]] or ["- ninguno"]
    lines.append("Commits recientes en el repositorio local (con archivos):")
    lines += [f"- {c['date'][:10]} {c['message']} · {', '.join(c['files'][:6])}" for c in local_commits[:15]] or ["- ninguno"]
    system = ("Eres un líder técnico que prepara un traspaso de trabajo. Con las issues abiertas y los commits recientes de una persona, "
              "escribe un resumen para quien la reemplaza: en qué estaba trabajando, qué parece quedar pendiente, qué partes del código "
              "tocó y por dónde retomar. Usa solo los datos entregados y cita issues y archivos. Si los datos no bastan, dilo. "
              + LANG.get(language, LANG["es"]))
    return _ask_model(engine, system, "\n".join(lines), max_tokens=800)


def plan_question(issue: dict, language: str = "es") -> str:
    body = (issue.get("body") or "").strip()[:1500]
    ref = issue.get("ref") or f"#{issue.get('number')}"
    if language == "en":
        return (f"Backlog task {ref}: {issue.get('title')}\n{body}\n\nPropose an implementation plan grounded on the code: numbered steps, "
                "the files and functions to change, the tests to add or update, the risks, and an estimate of the size (S, M or L) with "
                "the evidence that justifies it.")
    return (f"Tarea del backlog {ref}: {issue.get('title')}\n{body}\n\nPropón un plan de implementación basado en el código: pasos numerados, "
            "los archivos y funciones a modificar, las pruebas a agregar o actualizar, los riesgos y una estimación del tamaño (S, M o L) "
            "con la evidencia que la justifica.")


def _cos(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def related_issues(engine, issue: dict, candidates: list, top: int = 3) -> list:
    """Issues similar to this one (open or already solved): only a suggestion, so nobody repeats work."""
    others = [c for c in candidates if c.get("id") != issue.get("id")]
    if not others:
        return []
    cfg = engine.ai_config
    # Symmetric: the model's query prefix targets question-to-code retrieval, not issue-to-issue.
    vectors = embed_documents(engine.backend, cfg.embedding_model, [f"{c.get('title', '')}\n{(c.get('body') or '')[:800]}" for c in [issue, *others]])
    q, docs = vectors[0], vectors[1:]
    scored = sorted(((round(_cos(q, d), 3), c) for c, d in zip(others, docs)), key=lambda x: -x[0])
    return [{"similarity": s, **{k: c.get(k) for k in ("id", "ref", "title", "url", "state", "source")}} for s, c in scored[:top] if s >= RELATED_MIN]
