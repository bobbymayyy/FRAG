#!/usr/bin/env python3
"""
Exercise FRAG through the exact marketplace launcher.

The test covers the connection shape used by current MCP clients:

1. A 2026-aware stdio client may launch a short-lived probe and call
   `server/discover`. FRAG intentionally answers Method not found so the client
   classifies it as a 2025-era server.
2. The client launches the real session process and completes the legacy
   `initialize` + `tools/list` flow.

CI runs this script with a fresh Python venv and PIP_NO_INDEX=1. If
scripts/frag-server ever starts depending on pip, a preinstalled MCP package,
or another site dependency, this check fails.

It also deliberately supplies literal old-style userConfig placeholders while
leaving Claude's plugin-option variables unset. The MCP server must still come
online; credentials are required only when a host operation that needs them is
actually invoked.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

TIMEOUT = 30
PLUGIN_ROOT = Path(__file__).resolve().parent.parent
SERVER_SCRIPT = PLUGIN_ROOT / "scripts" / "frag-server"


def run_server(messages: list[dict[str, Any]], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    payload = "".join(json.dumps(message) + "\n" for message in messages)
    return subprocess.run(
        [sys.executable, str(SERVER_SCRIPT)],
        input=payload,
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
        env=env,
    )


def parse_responses(proc: subprocess.CompletedProcess[str]) -> tuple[dict[object, dict], list[str]]:
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
    return responses, unexpected_stdout


def assert_clean_process(proc: subprocess.CompletedProcess[str], label: str) -> bool:
    if proc.returncode != 0:
        print(
            f"FAIL: {label} launcher exited {proc.returncode}\nstderr:\n{proc.stderr[-3000:]}",
            file=sys.stderr,
        )
        return False

    _, unexpected_stdout = parse_responses(proc)
    if unexpected_stdout:
        print(
            f"FAIL: {label} wrote non-JSON text to MCP stdout:\n"
            + "\n".join(unexpected_stdout[-20:]),
            file=sys.stderr,
        )
        return False
    return True


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="frag-mcp-data-") as tmp:
        data = Path(tmp)
        env = os.environ.copy()
        env.update(
            {
                "FRAG_PLUGIN_ROOT": str(PLUGIN_ROOT),
                "FRAG_PLUGIN_DATA": str(data),
                "FRAG_HOME": str(data / "home"),
                # Compatibility input from an older .mcp.json. The new
                # launcher must discard these literal placeholders.
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
        for key in (
            "CLAUDE_PLUGIN_OPTION_GITHUB_TOKEN",
            "CLAUDE_PLUGIN_OPTION_GITHUB_DEFAULT_OWNER",
            "CLAUDE_PLUGIN_OPTION_GITEA_URL",
            "CLAUDE_PLUGIN_OPTION_GITEA_TOKEN",
            "CLAUDE_PLUGIN_OPTION_GITEA_DEFAULT_OWNER",
        ):
            env.pop(key, None)

        # Modern-era negotiation probe. MCP explicitly defines -32601 from a
        # stdio server as a signal to fall back to the legacy initialize path.
        try:
            probe = run_server(
                [
                    {
                        "jsonrpc": "2.0",
                        "id": "discover-1",
                        "method": "server/discover",
                        "params": {
                            "_meta": {
                                "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                                "io.modelcontextprotocol/clientInfo": {
                                    "name": "ci-handshake-check",
                                    "version": "1",
                                },
                                "io.modelcontextprotocol/clientCapabilities": {},
                            }
                        },
                    }
                ],
                env,
            )
        except subprocess.TimeoutExpired:
            print("FAIL: server/discover probe timed out", file=sys.stderr)
            return 1

        if not assert_clean_process(probe, "discover probe"):
            return 1
        probe_responses, _ = parse_responses(probe)
        probe_error = probe_responses.get("discover-1", {}).get("error", {})
        if probe_error.get("code") != -32601:
            print(
                f"FAIL: expected server/discover -32601 fallback, got {probe_responses!r}",
                file=sys.stderr,
            )
            return 1

        legacy_requests = [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "ci-handshake-check", "version": "1"},
                },
            },
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        ]

        try:
            proc = run_server(legacy_requests, env)
        except subprocess.TimeoutExpired:
            print("FAIL: legacy server did not respond within timeout", file=sys.stderr)
            return 1

    if not assert_clean_process(proc, "legacy session"):
        return 1

    responses, _ = parse_responses(proc)
    initialize = responses.get(1, {}).get("result", {})
    if initialize.get("protocolVersion") != "2025-11-25":
        print(
            f"FAIL: legacy protocol negotiation failed: {responses.get(1)!r}",
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

    print(
        "OK: modern probe fell back cleanly; marketplace legacy handshake "
        f"advertised {sorted(names)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
