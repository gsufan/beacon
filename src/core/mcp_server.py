"""Beacon as an MCP server (Model Context Protocol, stdio transport), so an editor or coding agent can ask the code,
explain an error, see why a piece of code is the way it is, measure the impact of a change and review the local
changes before a pull request.

It runs in the developer's own installation and calls the same functions as the local API, in the same process.
Messages are JSON-RPC 2.0, one per line on stdin and stdout; anything else goes to stderr so it cannot corrupt the
protocol.
"""

import json
import logging
import sys
from contextlib import redirect_stdout
from types import SimpleNamespace
from typing import Optional

from core.version import VERSION

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")

_PROJECT = {"type": "string", "description": "Beacon project id; optional when only one project is registered or the server was started with --project."}
TOOLS = [
    {"name": "beacon_ask",
     "description": "Ask a question about the project's code. The answer cites the files and functions it is based on.",
     "inputSchema": {"type": "object", "properties": {"question": {"type": "string"}, "project": _PROJECT}, "required": ["question"]}},
    {"name": "beacon_explain_error",
     "description": "Explain a stack trace or error output: locates each line in the project's code and says what it means, where it happens, its probable cause and what to check.",
     "inputSchema": {"type": "object", "properties": {"trace": {"type": "string"}, "project": _PROJECT}, "required": ["trace"]}},
    {"name": "beacon_why",
     "description": "Why a function (or a range of lines) is the way it is: the commits that wrote it, the issues they mention and an explanation.",
     "inputSchema": {"type": "object", "properties": {"name": {"type": "string", "description": "Function or method name."},
                                                      "file_path": {"type": "string"}, "start_line": {"type": "integer"},
                                                      "end_line": {"type": "integer"}, "project": _PROJECT}}},
    {"name": "beacon_impact",
     "description": "What depends on these functions: direct and indirect callers, the tests that exercise them and who knows the files.",
     "inputSchema": {"type": "object", "properties": {"targets": {"type": "array", "items": {"type": "string"}}, "project": _PROJECT},
                     "required": ["targets"]}},
    {"name": "beacon_review",
     "description": "Review the local changes before opening a pull request: changed functions and their callers, missing tests, tests worth running, verified knowledge that would become outdated, suggested reviewers and a description draft.",
     "inputSchema": {"type": "object", "properties": {"base": {"type": "string", "description": "Base branch (default origin/HEAD, main or master)."},
                                                      "describe": {"type": "boolean", "default": True}, "project": _PROJECT}}},
]


class ToolError(Exception):
    pass


def _request():
    """The local API functions only read the person (none here) and headers from the request."""
    return SimpleNamespace(state=SimpleNamespace(), headers={})


def _sources(sources: list, limit: int = 8) -> str:
    lines = [f"- {s['file_path']}:{s['start_line']}-{s['end_line']} {s.get('name') or ''}".rstrip() for s in sources[:limit]]
    return "\n\nFuentes:\n" + "\n".join(lines) if lines else ""


class BeaconTools:
    def __init__(self, default_project: Optional[str] = None, language: str = "es"):
        from core import api  # imported here: loading the whole API is only needed once the server runs
        self.api = api
        self.default_project = default_project
        self.language = language

    def _project(self, args: dict) -> str:
        pid = args.get("project") or self.default_project
        if pid:
            return pid
        projects = self.api.list_projects()
        if len(projects) == 1:
            return projects[0].id
        raise ToolError("Indica el proyecto (argumento «project»). Proyectos registrados: " + ", ".join(p.id for p in projects))

    def call(self, name: str, args: dict) -> str:
        api, lang = self.api, self.language
        pid = self._project(args)
        if name == "beacon_ask":
            r = api.query(pid, api.QueryRequest(question=args["question"], top_k=8, language=lang), _request())
            verified = r.get("verified")
            head = f"Respuesta verificada por {verified['reviewer']}:\n{verified['answer']}\n\n" if verified else ""
            return head + r["answer"] + _sources([s if isinstance(s, dict) else s.model_dump() for s in r["sources"]])
        if name == "beacon_explain_error":
            r = api.explain_error(pid, api.ErrorRequest(trace=args["trace"], language=lang), _request())
            located = [f"- {f['file_path']}:{f['line']} {f.get('name') or ''}".rstrip() for f in r["frames"] if f["file_path"]]
            return (r["explanation"] + ("\n\nTraza en el proyecto:\n" + "\n".join(located) if located else "") +
                    ("\n\nConocen estos archivos: " + ", ".join(e["name"] for e in r["experts"]) if r["experts"] else ""))
        if name == "beacon_why":
            body = api.WhyRequest(name=args.get("name"), file_path=args.get("file_path"), start_line=args.get("start_line"),
                                  end_line=args.get("end_line"), language=lang)
            r = api.why_code(pid, body, _request())
            commits = "\n".join(f"- {c['sha']} {c['date']} {c['author']}: {c['subject']}" for c in r["commits"])
            return f"{r['file_path']} ({r['start_line']}-{r['end_line']})\n\n{r['explanation']}\n\nCommits:\n{commits}"
        if name == "beacon_impact":
            r = api.change_impact(pid, api.ImpactRequest(targets=args["targets"]))
            out = []
            for t in r["targets"]:
                out.append(f"## {t['target']}\nLlamadores: " + (", ".join(f"{c['name']} ({c['file_path']})" for c in t["callers"]) or "ninguno") +
                           "\nIndirectos: " + (", ".join(f"{c['name']} ({c['file_path']})" for c in t["indirect"]) or "ninguno") +
                           "\nPruebas: " + (", ".join(sorted({c["file_path"] for c in t["tests"]})) or "ninguna"))
            if r.get("experts"):
                out.append("Conocen estos archivos: " + ", ".join(e["name"] for e in r["experts"]))
            return "\n\n".join(out)
        if name == "beacon_review":
            r = api.pre_pr_review(pid, api.ReviewRequest(base=args.get("base"), describe=args.get("describe", True), language=lang))
            if not r["files"]:
                return f"No hay cambios respecto de {r['base']}."
            lines = [f"Cambios de {r['branch'] or 'HEAD'} sobre {r['base']}: " + ", ".join(f"{f['status']} {f['path']}" for f in r["files"])]
            for fn in r["functions"]:
                lines.append(f"- {fn['name']} ({fn['file_path']}): " + ("nueva" if fn["new"] else f"{fn['callers']} llamadores") +
                             (", sin pruebas" if fn["missing_test"] else ""))
            if r["tests_to_run"]:
                lines.append("Pruebas que conviene correr: " + " ".join(r["tests_to_run"]))
            lines += [f"Respuesta verificada que podría quedar obsoleta: «{a['question']}»" for a in r["verified_answers"]]
            lines += [f"Documento verificado que quedará pendiente: {d['file_path']}" for d in r["verified_docs"]]
            if r["reviewers"]:
                lines.append("Revisores sugeridos: " + ", ".join(x["name"] for x in r["reviewers"]))
            if r["description"]:
                lines.append("\nBorrador de la descripción:\n" + r["description"])
            return "\n".join(lines)
        raise ToolError(f"Herramienta desconocida: {name}")


def handle(message: dict, tools) -> Optional[dict]:
    """The reply to one JSON-RPC message, or None for notifications."""
    method, mid = message.get("method"), message.get("id")
    if mid is None:
        return None  # notifications (initialized, cancelled) need no reply
    if method == "initialize":
        asked = (message.get("params") or {}).get("protocolVersion")
        result = {"protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
                  "capabilities": {"tools": {}}, "serverInfo": {"name": "beacon", "version": VERSION}}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        params = message.get("params") or {}
        try:
            text, error = tools.call(params.get("name", ""), params.get("arguments") or {}), False
        except ToolError as e:
            text, error = str(e), True
        except Exception as e:  # noqa: BLE001 - the error goes back to the client as the tool's result
            detail = getattr(e, "detail", None) or str(e) or type(e).__name__
            text, error = f"Beacon no pudo completar la acción: {detail}", True
        result = {"content": [{"type": "text", "text": text}], "isError": error}
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Método no soportado: {method}"}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def serve(default_project: Optional[str] = None, language: str = "es", stdin=None, stdout=None) -> None:
    stdin, out = stdin or sys.stdin, stdout or sys.stdout
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING, force=True)
    tools = None
    for line in stdin:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except ValueError:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "JSON no válido"}}
        else:
            if tools is None and message.get("method") == "tools/call":
                with redirect_stdout(sys.stderr):
                    tools = BeaconTools(default_project, language)
            with redirect_stdout(sys.stderr):  # nothing but protocol messages may reach stdout
                reply = handle(message, tools)
        if reply is not None:
            out.write(json.dumps(reply, ensure_ascii=False) + "\n")
            out.flush()
