# Changelog

All notable changes to FRAG are recorded here.

Note on versioning: the plugin's effective version is the **git commit SHA**,
because `plugins/frag/.claude-plugin/plugin.json` deliberately has no
`version` field. The version numbers below are for human bookkeeping and
match `plugins/frag/src/pyproject.toml`; they do not drive plugin updates.

## [Unreleased]

## [0.1.1]

### Fixed
- Replaced the removed `mcp.server.fastmcp.FastMCP` integration that broke
  when `fastmcp` 4.x resolved `mcp` 2.x. FRAG now implements its small MCP
  stdio surface directly with the Python standard library.
- Removed first-run venv creation and `pip install` from `frag-server`. MCP
  startup no longer needs PyPI/network access and works in restricted plugin
  sandboxes as long as Python 3.11+ is available.
- Removed `${user_config.*}` interpolation from `.mcp.json`. The launcher now
  consumes Claude Code's `CLAUDE_PLUGIN_OPTION_<KEY>` environment variables,
  so missing/unconfigured options cannot prevent the MCP process from spawning.
- The MCP handshake test now launches the actual marketplace `frag-server`
  path from a clean Python environment with `PIP_NO_INDEX=1` instead of
  bypassing the launcher with `python -m frag.mcp_server`.
- GitHub CI now gates both `latest` and `stable`, and uses current v7 releases
  of `actions/checkout` and `actions/setup-python` to avoid Node 20 runtime
  deprecation warnings on hosted runners.
- Gitea CI now mirrors validation for both `latest` and `stable` while keeping
  conservative action majors for `act_runner` compatibility.
- MCP search/resolve calls now close their SQLite stores after each request
  instead of leaking connections for the lifetime of the server process.
- Plugin license metadata now matches the repository's GPLv3 license.
- `install.sh` now defaults to `bobbymayyy/FRAG` and no longer requires the
  `venv` module for a runtime that does not use it.

## [0.1.0]

Initial release.

### Added
- Two-stage retrieval: FTS5 candidate generation, then optional cosine
  re-rank when an embedder is configured. Lexical-only is a fully supported
  mode, not a degraded one.
- Dual-host support for GitHub and Gitea via a `HostProvider` plugin slot,
  with reference grammar `host[/owner]/repo` (e.g. `github/CERBERUS-2.0`).
  References are accepted as an explicit argument or extracted from free text.
- Per-repo SQLite store. One repo is one file, which makes cross-repo scope
  leakage structurally impossible rather than something enforced by a
  `WHERE` clause.
- Content firewall: deny-list, magic-byte, NUL, UTF-8, and entropy checks,
  run on every touched file on every sync rather than only at first index.
- Delta sync driven by `git diff --name-only` between the old and new HEAD.
- MCP stdio server exposing `frag_search`, `frag_resolve`, and `frag_status`.
- Packaged as a Claude Code plugin with a bundled `frag-retrieval` skill that
  teaches Claude when to use retrieval instead of reading files wholesale.
- `install.sh` with preflight checks, idempotent re-runs, ref pinning, and
  dual-host source selection.
- CI gate on plugin distribution branches: unit tests, a real MCP handshake,
  and strict manifest validation.

### Design decisions worth remembering
- `chunks_fts` is a **plain** FTS5 table, not contentless. Contentless tables
  can't be deleted from with ordinary `DELETE ... WHERE rowid`, which breaks
  eviction.
- Plugins validate dependencies and credentials **eagerly in `__init__`**. A
  plugin that can't do its job must fail to construct rather than construct
  successfully and silently degrade while reporting itself active.
- `FRAG_HOME` lives in `${CLAUDE_PLUGIN_DATA}`, not `${CLAUDE_PLUGIN_ROOT}`.
  The latter is version-scoped and swept after updates, so indexes stored
  there would be wiped by plugin updates.
- The MCP server is launched as `python3 <script>` so a lost executable bit
  can't break the install.
