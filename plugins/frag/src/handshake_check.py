#!/usr/bin/env python3
"""
Spawn the MCP server and complete an initialize handshake plus a tools/list.

This is deliberately the *same contract* that frag-launch's self_check
enforces before promoting a version. Running it in CI means a build that
would fail to promote on a developer's machine fails in the pipeline
instead, where it's cheap to notice.

Exit 0 = the server speaks MCP and advertises its tools.
"""

from __future__ import annotations

import json
import subprocess
import sys

TIMEOUT = 30


def main() -> int:
    requests = [
        {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "ci-handshake-check", "version": "1"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ]
    payload = "".join(json.dumps(r) + "\n" for r in requests)

    try:
        proc = subprocess.run(
            [sys.executable, "-m", "frag.mcp_server"],
            input=payload, capture_output=True, text=True, timeout=TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        print("FAIL: server did not respond within timeout", file=sys.stderr)
        return 1

    responses = {}
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "id" in msg:
            responses[msg["id"]] = msg

    if 1 not in responses or "result" not in responses[1]:
        print(f"FAIL: no initialize result\nstderr:\n{proc.stderr[-2000:]}", file=sys.stderr)
        return 1

    tools_msg = responses.get(2, {})
    tools = tools_msg.get("result", {}).get("tools", [])
    names = {t.get("name") for t in tools}
    expected = {"frag_search", "frag_resolve", "frag_status"}
    missing = expected - names
    if missing:
        print(f"FAIL: server did not advertise tools: {sorted(missing)}", file=sys.stderr)
        return 1

    print(f"OK: handshake succeeded, tools advertised: {sorted(names)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
