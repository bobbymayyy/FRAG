"""
Dependency-free MCP stdio server.

FRAG deliberately implements the tiny MCP surface it needs instead of
pulling a Python MCP framework at plugin startup. The plugin runs inside
Claude Code environments that may be sandboxed or have no PyPI access, so
server startup must not require a network dependency install.

FRAG intentionally serves the 2025-era MCP lifecycle. Current MCP clients may
probe a stdio server with the 2026-07-28 `server/discover` method first; FRAG
answers that probe with JSON-RPC Method not found so standards-compliant
clients immediately fall back to the legacy `initialize` handshake. This is
preferable to claiming 2026-era support without implementing its stateless
per-request metadata and discovery contract.

Tool surface:

  frag_search(ref=None, query=..., top_k=8)
      Resolves the ref (or pulls one out of `query` if ref is omitted),
      syncs worktree+index, runs two-stage retrieval, returns fragments.

  frag_resolve(ref)
      Does the resolve/sync step and reports what changed without searching.

  frag_status(ref)
      Read-only: reports what's currently indexed without touching the
      network or re-syncing.

stdout is MCP JSON-RPC only. Any diagnostics belong on stderr.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from typing import Any

from frag.hosts import KNOWN_HOSTS, get_provider, parse_ref
from frag.resolve import _index_path  # internal but same package
from frag.resolve import resolve
from frag.retriever import search as run_search
from frag.store import Store

SERVER_NAME = "frag"
SERVER_VERSION = "0.1.1"
SUPPORTED_PROTOCOL_VERSIONS = (
    "2025-11-25",
    "2025-06-18",
    "2025-03-26",
    "2024-11-05",
)
DEFAULT_PROTOCOL_VERSION = SUPPORTED_PROTOCOL_VERSIONS[0]


def frag_search(query: str, ref: str | None = None, top_k: int = 8) -> dict:
    """Fragment a repo down to the pieces relevant to `query`. Provide `ref`
    (e.g. 'github/CERBERUS-2.0') explicitly when known; otherwise FRAG will
    try to find one in `query` itself."""
    handle = resolve(ref, free_text=query if ref is None else None)
    try:
        fragments = run_search(handle.store, query, top_k=top_k)
        return {
            "repo": handle.ref.key,
            "sync": {
                "accepted": handle.last_sync.accepted,
                "rejected": handle.last_sync.rejected,
                "evicted": handle.last_sync.evicted,
                "embedding_degraded": handle.last_sync.embedding_degraded,
                "degrade_reason": handle.last_sync.degrade_reason,
            },
            "fragments": [
                {
                    "path": f.path,
                    "start_line": f.start_line,
                    "end_line": f.end_line,
                    "text": f.text,
                    "score": f.score,
                }
                for f in fragments
            ],
        }
    finally:
        handle.store.close()


def frag_resolve(ref: str, force_full_resync: bool = False) -> dict:
    """Sync a repo's worktree and index without searching. Returns what
    changed on this sync."""
    handle = resolve(ref, force_full_resync=force_full_resync)
    try:
        return {
            "repo": handle.ref.key,
            "worktree": str(handle.worktree),
            "accepted": handle.last_sync.accepted,
            "rejected": handle.last_sync.rejected,
            "evicted": handle.last_sync.evicted,
            "embedding_degraded": handle.last_sync.embedding_degraded,
            "degrade_reason": handle.last_sync.degrade_reason,
        }
    finally:
        handle.store.close()


def frag_status(ref: str) -> dict:
    """Report what's currently indexed for a repo without touching the
    network. Fails clearly if nothing has been indexed yet."""
    parsed = parse_ref(ref, KNOWN_HOSTS)
    if parsed is None:
        raise ValueError(
            f"{ref!r} does not match host[/owner]/repo grammar or names an unknown host"
        )
    host, owner, repo = parsed
    provider = get_provider(host)
    repo_ref = provider.resolve(owner, repo)
    index_path = _index_path(repo_ref)
    if not index_path.exists():
        return {"repo": repo_ref.key, "indexed": False}

    store = Store(index_path)
    try:
        known = store.all_known_paths()
        fingerprint = store.get_fingerprint()
        return {
            "repo": repo_ref.key,
            "indexed": True,
            "known_files": len(known),
            "embedding_fingerprint": fingerprint,
        }
    finally:
        store.close()


TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "frag_search",
        "description": (
            "Fragment a repository down to the code relevant to a query. "
            "Synchronizes the repo/index before searching."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1},
                "ref": {
                    "type": "string",
                    "description": "Repository reference such as github/owner/repo.",
                },
                "top_k": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "default": 8,
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "frag_resolve",
        "description": "Synchronize a repository worktree and FRAG index without searching.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ref": {"type": "string", "minLength": 1},
                "force_full_resync": {"type": "boolean", "default": False},
            },
            "required": ["ref"],
            "additionalProperties": False,
        },
    },
    {
        "name": "frag_status",
        "description": "Report the locally indexed status of a repository without syncing it.",
        "inputSchema": {
            "type": "object",
            "properties": {"ref": {"type": "string", "minLength": 1}},
            "required": ["ref"],
            "additionalProperties": False,
        },
    },
]

ToolHandler = Callable[..., dict]
TOOL_HANDLERS: dict[str, ToolHandler] = {
    "frag_search": frag_search,
    "frag_resolve": frag_resolve,
    "frag_status": frag_status,
}


def _rpc_result(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _rpc_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def _tool_result(value: dict) -> dict[str, Any]:
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(value, ensure_ascii=False, separators=(",", ":")),
            }
        ]
    }


def _tool_error(exc: Exception) -> dict[str, Any]:
    return {
        "content": [
            {
                "type": "text",
                "text": f"{type(exc).__name__}: {exc}",
            }
        ],
        "isError": True,
    }


def handle_message(message: dict[str, Any]) -> dict[str, Any] | None:
    """Handle one JSON-RPC message. Notifications return None."""
    has_id = "id" in message
    request_id = message.get("id")
    method = message.get("method")

    if not isinstance(method, str):
        return _rpc_error(request_id, -32600, "Invalid Request") if has_id else None

    if method == "notifications/initialized":
        return None

    # Other notifications do not require responses. Keeping this generic also
    # makes cancellation/logging notifications harmless across MCP revisions.
    if not has_id:
        return None

    params = message.get("params", {})

    if method == "server/discover":
        # We intentionally advertise ourselves as a legacy/2025-era server.
        # MCP 2026-aware stdio clients use -32601 as a defined fallback signal.
        return _rpc_error(request_id, -32601, "Method not found: server/discover")

    if method == "initialize":
        if not isinstance(params, dict):
            return _rpc_error(request_id, -32602, "initialize params must be an object")
        requested = params.get("protocolVersion")
        protocol = (
            requested
            if isinstance(requested, str) and requested in SUPPORTED_PROTOCOL_VERSIONS
            else DEFAULT_PROTOCOL_VERSION
        )
        return _rpc_result(
            request_id,
            {
                "protocolVersion": protocol,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        )

    if method == "ping":
        return _rpc_result(request_id, {})

    if method == "tools/list":
        return _rpc_result(request_id, {"tools": TOOL_DEFINITIONS})

    if method == "tools/call":
        if not isinstance(params, dict):
            return _rpc_error(request_id, -32602, "tools/call params must be an object")
        name = params.get("name")
        arguments = params.get("arguments", {})
        if not isinstance(name, str) or name not in TOOL_HANDLERS:
            return _rpc_error(request_id, -32602, f"unknown tool: {name!r}")
        if not isinstance(arguments, dict):
            return _rpc_error(request_id, -32602, "tool arguments must be an object")
        try:
            value = TOOL_HANDLERS[name](**arguments)
        except Exception as exc:  # tool failures are MCP CallToolResult errors
            return _rpc_result(request_id, _tool_error(exc))
        return _rpc_result(request_id, _tool_result(value))

    return _rpc_error(request_id, -32601, f"Method not found: {method}")


def _write(message: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def main() -> None:
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            message = json.loads(raw)
        except json.JSONDecodeError as exc:
            _write(_rpc_error(None, -32700, f"Parse error: {exc.msg}"))
            continue
        if not isinstance(message, dict):
            _write(_rpc_error(None, -32600, "Invalid Request"))
            continue
        try:
            response = handle_message(message)
        except Exception as exc:  # protocol-level guardrail; tools are handled above
            print(f"[frag-mcp] internal error: {type(exc).__name__}: {exc}", file=sys.stderr)
            if "id" not in message:
                continue
            response = _rpc_error(message.get("id"), -32603, "Internal error")
        if response is not None:
            try:
                _write(response)
            except BrokenPipeError:
                return


if __name__ == "__main__":
    main()
