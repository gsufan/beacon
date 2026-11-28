"""MCP server: the JSON-RPC handshake, the tool list, tool calls and errors, and the stdio loop (no model server)."""
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core import mcp_server  # noqa: E402


class FakeTools:
    def call(self, name, args):
        if name == "boom":
            raise RuntimeError("se cayó")
        if name == "missing":
            raise mcp_server.ToolError("Indica el proyecto")
        return f"{name}:{args.get('question', '')}"


def test_handshake_and_tool_list():
    r = mcp_server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}}, None)
    assert r["result"]["protocolVersion"] == "2025-03-26" and r["result"]["serverInfo"]["name"] == "beacon"
    unknown = mcp_server.handle({"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": {"protocolVersion": "1999-01-01"}}, None)
    assert unknown["result"]["protocolVersion"] == mcp_server.PROTOCOL_VERSIONS[0]
    assert mcp_server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}, None) is None
    names = [t["name"] for t in mcp_server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/list"}, None)["result"]["tools"]]
    assert names == ["beacon_ask", "beacon_explain_error", "beacon_why", "beacon_impact", "beacon_review"]
    assert mcp_server.handle({"jsonrpc": "2.0", "id": 4, "method": "resources/list"}, None)["error"]["code"] == -32601


def test_tool_calls_and_errors():
    call = lambda name, args=None: mcp_server.handle(  # noqa: E731
        {"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {"name": name, "arguments": args or {}}}, FakeTools())["result"]
    ok = call("beacon_ask", {"question": "¿qué hace total?"})
    assert ok == {"content": [{"type": "text", "text": "beacon_ask:¿qué hace total?"}], "isError": False}
    assert call("missing") == {"content": [{"type": "text", "text": "Indica el proyecto"}], "isError": True}
    assert call("boom")["isError"] and "se cayó" in call("boom")["content"][0]["text"]


def test_beacon_tools_use_the_local_api(monkeypatch):
    import core.api as api
    monkeypatch.setattr(api, "query", lambda pid, req, request: {
        "answer": f"{pid}: suma los precios", "verified": None,
        "sources": [{"file_path": "src/cart.py", "start_line": 1, "end_line": 4, "name": "total"}]})
    monkeypatch.setattr(api, "change_impact", lambda pid, body: {
        "targets": [{"target": body.targets[0], "callers": [{"name": "pay", "file_path": "src/pay.py"}], "indirect": [], "tests": []}],
        "experts": [{"name": "Ana"}]})
    monkeypatch.setattr(api, "list_projects", lambda: [type("P", (), {"id": "shop"})(), type("P", (), {"id": "web"})()])
    tools = mcp_server.BeaconTools(default_project="shop")
    text = tools.call("beacon_ask", {"question": "¿Qué hace total?"})
    assert text.startswith("shop: suma los precios") and "src/cart.py:1-4 total" in text
    impact = tools.call("beacon_impact", {"targets": ["total"]})
    assert "pay (src/pay.py)" in impact and "Pruebas: ninguna" in impact and "Ana" in impact
    try:
        mcp_server.BeaconTools().call("beacon_ask", {"question": "x"})
    except mcp_server.ToolError as e:
        assert "shop, web" in str(e)
    else:
        raise AssertionError("debía pedir el proyecto")


def test_stdio_loop_writes_only_protocol(monkeypatch):
    monkeypatch.setattr(mcp_server, "BeaconTools", lambda project, language: FakeTools())
    lines = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
             {"jsonrpc": "2.0", "method": "notifications/initialized"},
             {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "beacon_ask", "arguments": {"question": "hola"}}}]
    stdin = io.StringIO("\n".join(json.dumps(m) for m in lines) + "\nno es json\n")
    stdout = io.StringIO()
    mcp_server.serve(stdin=stdin, stdout=stdout)
    replies = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert [r.get("id") for r in replies] == [1, 2, None]
    assert replies[1]["result"]["content"][0]["text"] == "beacon_ask:hola" and replies[2]["error"]["code"] == -32700
