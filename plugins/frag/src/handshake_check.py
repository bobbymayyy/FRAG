#!/usr/bin/env python3
"""
Spawn FRAG through the exact marketplace launcher and complete an MCP
initialize + tools/list handshake.

The important part is the launch path: CI runs this script with a fresh Python
venv and PIP_NO_INDEX=1. If scripts/frag-server ever starts depending on pip,
a preinstalled MCP package, or another site dependency, this check fails.

It also deliberately supplies literal userConfig placeholders, matching the
failure mode seen when optional plugin configuration has not been filled in.
The MCP server must still come online; credentials are required only when a
host operation that needs them is actually invoked.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

TIMEOUT = 30
PLUGIN_ROOT = Path(__file__).resolve().parent.parent
SERVER_SCRIPT = PLUGIN_ROOT / "scripts" / "frag-server"


def main() -> int:
    requests = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "ci-handshake-check", "version": "1"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ]
    payload = "".join(json.dumps(request) + "\n" for request in requests)

    with tempfile.TemporaryDirectory(prefix="frag-mcp-data-") as tmp:
        data = Path(tmp)
        env = os.environ.copy()
        env.update(
            {
                "FRAG_PLUGIN_ROOT": str(PLUGIN_ROOT),
                "FRAG_PLUGIN_DATA": str(data),
                "FRAG_HOME": str(data / "home"),
                "FRAG_GITHUB_TOKEN": "${user_config.github_token}",
                "FRAG_GITHUB_DEFAULT_OWNER": "${user_config.github_default_owner}",
                "FRAG_GITEA_URL": "${user_config.gitea_url}",
                "FRAG_GITEA_TOKEN": "${user_config.gitea_token}",
                "FRAG_GITEA_DEFAULT_OWNER": "${user_config.gitea_default_owner}",
                # If the launcher regresses to installing dependencies, fail
                # instead of silently succeeding because CI has network.
                "PIP_NO_INDEX": "1",
            }
        )

        try:
            proc = subprocess.run(
                [sys.executable, str(SERVER_SCRIPT)],
                input=payload,
                capture_output=True,
                text=True,
                timeout=TIMEOUT,
                env=env,
            )
        except subprocess.TimeoutExpired:
            print("FAIL: server did not respond within timeout", file=sys.stderr)
            return 1

    responses: dict[object, dict] = {}
    unexpected_stdout: list[str] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            unexpected_stdout.append(line)
            continue
        if "id" in msg:
            responses[msg["id"]] = msg

    if proc.returncode != 0:
        print(
            f"FAIL: launcher exited {proc.returncode}\nstderr:\n{proc.stderr[-3000:]}",
            file=sys.stderr,
        )
        return 1

    if unexpected_stdout:
        print(
            "FAIL: non-JSON text was written to MCP stdout:\n"
            + "\n".join(unexpected_stdout[-20:]),
            file=sys.stderr,
        )
        return 1

    if 1 not in responses or "result" not in responses[1]:
        print(
            f"FAIL: no initialize result\nstderr:\n{proc.stderr[-3000:]}",
            file=sys.stderr,
        )
        return 1

    tools_msg = responses.get(2, {})
    tools = tools_msg.get("result", {}).get("tools", [])
    names = {tool.get("name") for tool in tools}
    expected = {"frag_search", "frag_resolve", "frag_status"}
    missing = expected - names
    if missing:
        print(
            f"FAIL: server did not advertise tools: {sorted(missing)}",
            file=sys.stderr,
        )
        return 1

    print(f"OK: marketplace launcher handshake succeeded: {sorted(names)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
