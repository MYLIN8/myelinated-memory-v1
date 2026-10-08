#!/usr/bin/env python3
"""Myelinated Memory as an MCP server - portable to any MCP client.

    python3 scripts/myelinated_mcp.py --store /path/to/store.json
    python3 scripts/myelinated_mcp.py --selftest

Speaks the Model Context Protocol over stdio (JSON-RPC 2.0, newline-delimited),
so Claude Code, Claude Desktop, or any other MCP client can use the engine as a
memory tool. Zero dependencies: the framing is stdlib only, and the engine is
imported from the same single file the CLI uses.

The server wraps `MyelinatedMemory` behind seven tools - memory_add,
memory_access, memory_retire, memory_pin, memory_recall, memory_refresh and
memory_stats - so the session protocol in SKILL.md becomes ordinary tool calls.

`--selftest` runs an in-process round trip (initialize -> list tools -> add ->
recall -> retire) without needing a client, and exits non-zero if anything is
wrong. It writes nothing outside the temporary store it creates.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from typing import Any, Dict, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from myelinate import MyelinatedMemory  # noqa: E402

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "myelinated-memory", "version": "5.1.0"}

# One JSON Schema per tool. Kept flat and honest: every field the engine
# actually reads is here, and nothing more.
TOOLS = [
    {
        "name": "memory_add",
        "description": ("Store a memory. Near-duplicates collapse into the existing entry; "
                        "a same-subject pair with changed wording supersedes the older one. "
                        "Categories set the decay rate: identity 0.002, preference/user 0.010, "
                        "general 0.030 (default), task 0.050, ephemeral 0.100 per dormant day."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "The fact to remember."},
                "category": {"type": "string",
                             "description": "Decay class; default 'general'."},
                "protected": {"type": "boolean",
                              "description": "Pin: never decays, never crowded out."},
                "supersedes": {"type": "string",
                               "description": "id of the memory this one replaces."},
            },
            "required": ["content"],
        },
    },
    {
        "name": "memory_access",
        "description": "Strengthen a memory because it was actually used (Hebbian boost).",
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
        },
    },
    {
        "name": "memory_retire",
        "description": "Stop surfacing a memory whose fact has changed or been replaced.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "superseded_by": {"type": "string",
                                  "description": "id of the replacement memory."},
            },
            "required": ["id"],
        },
    },
    {
        "name": "memory_pin",
        "description": "Protect a memory from decay and pruning (safety-critical or identity facts).",
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
        },
    },
    {
        "name": "memory_recall",
        "description": ("Context injection: fills a character budget with the memories that best "
                        "answer the query, rendered at full/summary/gist detail by expected value "
                        "per character. Returns the packed text plus the ids used."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string",
                          "description": "The question. Without it, ranking is query-blind."},
                "budget": {"type": "integer",
                           "description": "Character budget; default 2200."},
                "force_similarity": {"type": "boolean",
                                     "description": ("Pin query-similarity ranking on/off for "
                                                     "this recall; omit to use the engine default.")},
            },
        },
    },
    {
        "name": "memory_refresh",
        "description": ("Session boundary: fold accrued decay, consolidate near-duplicates, "
                        "optionally prune to a storage ceiling (weakest archived entries only)."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "max_entries": {"type": "integer",
                                "description": "Storage ceiling; 0 or omit keeps everything."},
            },
        },
    },
    {
        "name": "memory_stats",
        "description": "Tier distribution and mean strength, derived at the current clock.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


class McpServer:
    """JSON-RPC 2.0 dispatch over one engine instance."""

    def __init__(self, engine: MyelinatedMemory):
        self.engine = engine

    # ------------------------------------------------------------ transport
    def handle_line(self, line: str) -> Optional[str]:
        """One request line in, one response line (or None for notifications)."""
        line = line.strip()
        if not line:
            return None
        try:
            message = json.loads(line)
        except ValueError:
            return self._error(None, -32700, "parse error")
        response = self.handle_message(message)
        return None if response is None else json.dumps(response)

    def handle_message(self, message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        method = message.get("method")
        msg_id = message.get("id")
        params = message.get("params") or {}
        if method == "initialize":
            return self._result(msg_id, {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": SERVER_INFO,
            })
        if method in ("notifications/initialized", "notifications/cancelled"):
            return None  # notifications get no response
        if method == "ping":
            return self._result(msg_id, {})
        if method == "tools/list":
            return self._result(msg_id, {"tools": TOOLS})
        if method == "tools/call":
            return self._call_tool(msg_id, params)
        return self._error(msg_id, -32601, "method not found: %s" % method)

    # --------------------------------------------------------------- tools
    def _call_tool(self, msg_id: Any, params: Dict[str, Any]) -> Dict[str, Any]:
        name = params.get("name")
        args = params.get("arguments") or {}
        try:
            result = self._dispatch(name, args)
        except (KeyError, ValueError) as exc:
            return self._result(msg_id, {
                "content": [{"type": "text", "text": str(exc)}],
                "isError": True,
            })
        return self._result(msg_id, {
            "content": [{"type": "text", "text": json.dumps(result, sort_keys=True)}],
            "isError": False,
        })

    def _dispatch(self, name: Optional[str], args: Dict[str, Any]) -> Dict[str, Any]:
        engine = self.engine
        if name == "memory_add":
            content = args.get("content")
            if not content:
                raise ValueError("content must not be empty")
            memory_id = engine.add(
                content,
                category=args.get("category", "general"),
                protected=bool(args.get("protected", False)),
                supersedes=args.get("supersedes"),
            )
            engine.save()
            return {"id": memory_id}
        if name in ("memory_access", "memory_retire", "memory_pin"):
            memory_id = args.get("id")
            if not memory_id:
                raise ValueError("id is required")
            if name == "memory_access":
                ok = engine.access(memory_id)
            elif name == "memory_retire":
                ok = engine.retire(memory_id, superseded_by=args.get("superseded_by"))
            else:
                ok = engine.pin(memory_id)
            engine.save()
            return {"ok": bool(ok), "id": memory_id}
        if name == "memory_recall":
            result = engine.recall(
                budget=int(args.get("budget", 2200)),
                query=args.get("query"),
                force_similarity=args.get("force_similarity"),
            )
            return {
                "text": result.text,
                "used_ids": result.used_ids,
                "ranked_ids": result.ranked_ids[:10],
                "tiers": result.tiers,
                "chars": result.chars,
                "budget": result.budget,
            }
        if name == "memory_refresh":
            report = engine.refresh(max_entries=int(args.get("max_entries", 0)))
            engine.save()
            return report
        if name == "memory_stats":
            return engine.stats()
        raise ValueError("unknown tool: %s" % name)

    # ----------------------------------------------------------- protocol
    @staticmethod
    def _result(msg_id: Any, result: Dict[str, Any]) -> Dict[str, Any]:
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    @staticmethod
    def _error(msg_id: Any, code: int, message: str) -> Dict[str, Any]:
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


# ---------------------------------------------------------------- selftest
def selftest() -> int:
    """Round trip through the protocol surface, no client needed."""
    checks = 0

    def ok(condition: bool, message: str) -> None:
        nonlocal checks
        checks += 1
        assert condition, message

    with tempfile.TemporaryDirectory() as tmp:
        engine = MyelinatedMemory(path=os.path.join(tmp, "store.json"))
        server = McpServer(engine)

        reply = json.loads(server.handle_line(
            '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}') or "{}")
        ok(reply.get("result", {}).get("serverInfo", {}).get("name") == "myelinated-memory",
           "initialize must identify the server")

        reply = json.loads(server.handle_line(
            '{"jsonrpc":"2.0","id":2,"method":"tools/list"}') or "{}")
        names = [tool["name"] for tool in reply.get("result", {}).get("tools", [])]
        ok(names == [tool["name"] for tool in TOOLS], "tools/list must return every tool")

        reply = json.loads(server.handle_line(json.dumps({
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "memory_add", "arguments": {
                "content": "The deploy target is production.", "category": "identity"}}})) or "{}")
        payload = json.loads(reply["result"]["content"][0]["text"])
        memory_id = payload.get("id")
        ok(bool(memory_id), "memory_add must return an id")

        reply = json.loads(server.handle_line(json.dumps({
            "jsonrpc": "2.0", "id": 4, "method": "tools/call",
            "params": {"name": "memory_recall", "arguments": {
                "query": "where is the deploy target", "budget": 2200}}})) or "{}")
        payload = json.loads(reply["result"]["content"][0]["text"])
        ok(memory_id in payload.get("used_ids", []), "recall must surface the stored memory")
        ok(payload.get("chars", 0) <= 2200, "recall must respect the budget")
        ok("production" in payload.get("text", ""), "recall text must carry the fact")

        reply = json.loads(server.handle_line(json.dumps({
            "jsonrpc": "2.0", "id": 5, "method": "tools/call",
            "params": {"name": "memory_retire", "arguments": {"id": memory_id}}})) or "{}")
        payload = json.loads(reply["result"]["content"][0]["text"])
        ok(payload.get("ok") is True, "retire must confirm")

        reply = json.loads(server.handle_line(json.dumps({
            "jsonrpc": "2.0", "id": 6, "method": "tools/call",
            "params": {"name": "memory_recall", "arguments": {"query": "deploy target"}}})) or "{}")
        payload = json.loads(reply["result"]["content"][0]["text"])
        ok(memory_id not in payload.get("used_ids", []), "a retired memory must not surface")

        reply = json.loads(server.handle_line(
            '{"jsonrpc":"2.0","id":7,"method":"no/such/method"}') or "{}")
        ok(reply.get("error", {}).get("code") == -32601, "unknown methods must error")

        ok(server.handle_line('{"jsonrpc":"2.0","method":"notifications/initialized"}') is None,
           "notifications get no response")

    print("mcp ok: %d checks" % checks)
    return 0


# -------------------------------------------------------------------- main
def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(prog="myelinated_mcp",
                                    description="Myelinated Memory as an MCP server (stdio)")
    parser.add_argument("--store", default=None, help="path to the memory store")
    parser.add_argument("--selftest", action="store_true",
                        help="run an in-process protocol round trip and exit")
    args = parser.parse_args(argv)

    if args.selftest:
        return selftest()

    engine = MyelinatedMemory(path=args.store)
    server = McpServer(engine)
    for line in sys.stdin:
        response = server.handle_line(line)
        if response is not None:
            sys.stdout.write(response + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
