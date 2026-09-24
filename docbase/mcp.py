"""An MCP server over stdio.

The CLI already makes the base usable from anything that can run a command,
but an agent then has to shell out and parse text. Speaking MCP makes search a
native tool call instead, with a typed schema the model can see.

Implemented directly against the protocol rather than through an SDK. MCP over
stdio is JSON-RPC 2.0 on stdin and stdout, which is a few hundred lines — and
a dependency here would undo the thing that makes the rest of the tool easy to
install.

    docbase serve

Wire it into a client's config as the command to run; the working directory
decides which knowledge base is served.
"""
from __future__ import annotations

import io
import json
import os
import sys

from . import config as config_module

#: Versions whose stdio framing and method names match what is implemented.
KNOWN_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
DEFAULT_PROTOCOL = "2024-11-05"

SERVER_INFO = {"name": "docbase", "version": "0.1.0"}

#: A tool answer is spent from the caller's context. Listing five hundred
#: documents with their sections cost fifty-seven thousand tokens — more than
#: the whole base exists to save.
LIST_DOCUMENT_CAP = 200

TOOLS = [
    {
        "name": "search_documentation",
        "description": (
            "Search the local documentation base and return the most relevant "
            "fragments, with the file and line they came from. Use this instead "
            "of answering from general knowledge whenever the question is about "
            "the user's own policies, procedures or internal documentation. "
            "Returns a confidence score; a low one means the wording of the "
            "question did not match the wording of the documentation, so try "
            "again with the terms the documentation would use."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string",
                          "description": "Words to search for."},
                "limit": {"type": "integer",
                          "description": "How many fragments to return.",
                          "default": 8},
            },
            "required": ["query"],
        },
    },
    {
        "name": "list_documents",
        "description": (
            "What the base covers. Called with no argument it lists the "
            "documents, with their sections when there are few enough to be "
            "worth reading. Name a document to see that one's sections and "
            "their line numbers. Use it before concluding an answer is "
            "missing, or to choose where to read directly."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "document": {"type": "string",
                             "description": "Name or title fragment. Omit for "
                                            "the whole base."},
            },
        },
    },
    {
        "name": "read_section",
        "description": (
            "Read a document from a given line. Use after a search points at a "
            "section whose fragment was cut short."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "file": {"type": "string",
                         "description": "Document name as reported by search."},
                "line": {"type": "integer", "description": "First line to read."},
                "lines": {"type": "integer", "default": 40,
                          "description": "How many lines to read."},
            },
            "required": ["file"],
        },
    },
    {
        "name": "base_status",
        "description": (
            "Report what is stale, what changed recently, and which referenced "
            "documents have not been imported."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "sync_base",
        "description": (
            "Import newly added files and refresh anything whose source changed. "
            "Cheap and safe to call; it does nothing when nothing changed."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
]


# --------------------------------------------------------------- tool bodies
def _capture(function, *args, **kwargs):
    """Run something that prints, and return what it printed."""
    buffer = io.StringIO()
    stdout, sys.stdout = sys.stdout, buffer
    try:
        function(*args, **kwargs)
    finally:
        sys.stdout = stdout
    return buffer.getvalue().strip()


def _search(cfg, arguments):
    from .search import Index

    query = (arguments.get("query") or "").strip()
    if not query:
        return "No query given."
    index = Index(cfg).build()
    if not index.files():
        return "The base is empty. Add a document and call sync_base."

    hits = index.search(query, limit=int(arguments.get("limit") or
                                         cfg.search.default_hits))
    if not hits:
        return ("Nothing matched. This topic may not be in the base — "
                "call list_documents before concluding the answer is missing.")

    lines = []
    if hits[0][0] < cfg.search.low_confidence:
        lines.append(
            f"LOW CONFIDENCE (best {hits[0][0]:.2f} < {cfg.search.low_confidence}). "
            "The wording of the question may not match the documentation; "
            "consider searching again with its terms, or call list_documents.")
        lines.append("")

    budget = cfg.search.total_chars
    for score, name, line_no, heading, body in hits:
        text = body[:min(cfg.search.per_hit_chars, budget)]
        lines.append(f"--- {name}:{line_no}  [{score:.2f}]  {heading or '(no heading)'}")
        lines.append(text)
        lines.append("")
        budget -= len(text)
        if budget <= 0:
            break
    return "\n".join(lines).strip()


def _list_documents(cfg, arguments=None):
    from .search import Index, outline

    index = Index(cfg).build()
    wanted = ((arguments or {}).get("document") or "").strip()
    return "\n".join(outline(index, wanted, cap=LIST_DOCUMENT_CAP))


def _read_section(cfg, arguments):
    from .search import Index

    wanted = (arguments.get("file") or "").strip()
    start = max(int(arguments.get("line") or 1), 1)
    count = min(int(arguments.get("lines") or 40), 200)

    for name, path in Index(cfg).files():
        if name == wanted or os.path.basename(name) == wanted:
            lines = open(path, encoding="utf-8").read().splitlines()
            chunk = lines[start - 1:start - 1 + count]
            return "\n".join(chunk) if chunk else "(past the end of the document)"
    return f"No document called {wanted!r}. Call list_documents for the names."


def call_tool(cfg, name, arguments):
    arguments = arguments or {}
    if name == "search_documentation":
        return _search(cfg, arguments)
    if name == "list_documents":
        return _list_documents(cfg, arguments)
    if name == "read_section":
        return _read_section(cfg, arguments)
    if name == "base_status":
        from . import status
        return _capture(status.report, cfg) or "Nothing to report."
    if name == "sync_base":
        from . import sync
        return _capture(sync.run, cfg) or "Already up to date."
    raise KeyError(name)


# ------------------------------------------------------------------ plumbing
def _result(request_id, payload):
    return {"jsonrpc": "2.0", "id": request_id, "result": payload}


def _error(request_id, code, message):
    return {"jsonrpc": "2.0", "id": request_id,
            "error": {"code": code, "message": message}}


def handle(message, cfg):
    """One request -> one response, or None for a notification."""
    method = message.get("method")
    request_id = message.get("id")

    if method == "initialize":
        asked = (message.get("params") or {}).get("protocolVersion")
        version = asked if asked in KNOWN_PROTOCOLS else DEFAULT_PROTOCOL
        return _result(request_id, {
            "protocolVersion": version,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        })

    if method in ("notifications/initialized", "initialized"):
        return None                       # notifications get no reply

    if method == "ping":
        return _result(request_id, {})

    if method == "tools/list":
        return _result(request_id, {"tools": TOOLS})

    if method == "tools/call":
        params = message.get("params") or {}
        name = params.get("name")
        try:
            text = call_tool(cfg, name, params.get("arguments"))
        except KeyError:
            return _error(request_id, -32602, f"unknown tool: {name}")
        except Exception as error:                     # noqa: BLE001
            # A failing tool is a result the model can react to, not a
            # transport error that kills the session.
            return _result(request_id, {
                "content": [{"type": "text", "text": f"Tool failed: {error}"}],
                "isError": True,
            })
        return _result(request_id, {"content": [{"type": "text", "text": text}]})

    if request_id is None:
        return None                       # unknown notification: ignore
    return _error(request_id, -32601, f"unknown method: {method}")


def serve(cfg=None, stdin=None, stdout=None):
    """Read line-delimited JSON-RPC until the input closes."""
    cfg = cfg or config_module.load()
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout

    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            stdout.write(json.dumps(_error(None, -32700, "parse error")) + "\n")
            stdout.flush()
            continue

        response = handle(message, cfg)
        if response is not None:
            stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            stdout.flush()
    return 0
