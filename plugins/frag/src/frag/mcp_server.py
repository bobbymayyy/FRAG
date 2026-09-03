"""Dependency-free MCP stdio server for FRAG.

The default retrieval path is local-first when a repository hub is available:
working clone -> bare mirror -> archive snapshot -> remote provider.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from typing import Any

from frag.local_sources import SOURCE_KINDS
from frag.resolve import _index_path, _read_source_marker, resolve, resolve_index_ref
from frag.retriever import search as run_search
from frag.store import Store

SERVER_NAME = "frag"
SERVER_VERSION = "0.2.0"
SUPPORTED_PROTOCOL_VERSIONS = (
    "2025-11-25",
    "2025-06-18",
    "2025-03-26",
    "2024-11-05",
)
DEFAULT_PROTOCOL_VERSION = SUPPORTED_PROTOCOL_VERSIONS[0]
SOURCE_ENUM = ["auto", "worktree", "mirror", "archive", "remote"]


def frag_search(
    query: str,
    ref: str | None = None,
    top_k: int = 8,
    source: str = "auto",
) -> dict:
    """Fragment a repo down to the pieces relevant to ``query``."""
    handle = resolve(ref, free_text=query if ref is None else None, source=source)
    try:
        fragments = run_search(handle.store, query, top_k=top_k)
        return {
            "repo": handle.ref.key,
            "source": handle.source_kind,
            "source_path": str(handle.source_path),
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


def frag_resolve(
    ref: str,
    force_full_resync: bool = False,
    source: str = "auto",
) -> dict:
    """Acquire a repo source and bring its FRAG index current."""
    handle = resolve(ref, force_full_resync=force_full_resync, source=source)
    try:
        return {
            "repo": handle.ref.key,
            "source": handle.source_kind,
            "source_path": str(handle.source_path),
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
    """Report what's currently indexed without acquiring or syncing source."""
    repo_ref = resolve_index_ref(ref)
    index_path = _index_path(repo_ref)
    if not index_path.exists():
        return {"repo": repo_ref.key, "indexed": False}

    store = Store(index_path)
    try:
        known = store.all_known_paths()
        fingerprint = store.get_fingerprint()
        marker = _read_source_marker(repo_ref)
        source_kind = marker.split(":", 1)[0] if marker else None
        return {
            "repo": repo_ref.key,
            "indexed": True,
            "known_files": len(known),
            "embedding_fingerprint": fingerprint,
            "source": source_kind,
            "source_identity": marker,
        }
    finally:
        store.close()


def _source_schema() -> dict[str, Any]:
    return {
        "type": "string",
        "enum": SOURCE_ENUM,
        "default": "auto",
        "description": (
            "Where repository bytes come from. auto prefers the local hub in order: "
            "worktree, mirror, archive, then remote."
        ),
    }


TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "frag_search",
        "description": (
            "Fragment a repository down to code relevant to a query. Uses an existing local "
            "repository hub before network cloning when available."
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
                "source": _source_schema(),
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "frag_resolve",
        "description": (
            "Acquire a repository source and synchronize its FRAG index without searching. "
            "Defaults to local-first source selection."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ref": {"type": "string", "minLength": 1},
                "force_full_resync": {"type": "boolean", "default": False},
                "source": _source_schema(),
            },
            "required": ["ref"],
            "additionalProperties": False,
        },
    },
    {
        "name": "frag_status",
        "description": "Report locally indexed repository status without syncing or network access.",
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
    has_id = "id" in message
    request_id = message.get("id")
    method = message.get("method")

    if not isinstance(method, str):
        return _rpc_error(request_id, -32600, "Invalid Request") if has_id else None

    if method == "notifications/initialized":
        return None
    if not has_id:
        return None

    params = message.get("params", {})

    if method == "server/discover":
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
        except Exception as exc:
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
        except Exception as exc:
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
