# Changelog

All notable changes to FRAG are recorded here.

Note on versioning: the plugin's effective version is the **git commit SHA**,
because `plugins/frag/.claude-plugin/plugin.json` deliberately has no
`version` field. The version numbers below are human bookkeeping only.

## [Unreleased]

## [0.2.0]

### Added
- Local-first repository acquisition. `source="auto"` now tries a repository
  hub working clone, bare mirror, newest archive snapshot, then the existing
  GitHub/Gitea remote provider.
- Explicit `worktree`, `mirror`, `archive`, and `remote` source modes on
  `frag_search` and `frag_resolve`.
- Automatic `/srv/repos` discovery plus optional `repo_hub` / `FRAG_REPO_HUB`
  configuration for other repository hubs.
- Read-only mirror/archive materialization under `FRAG_HOME`; managed hub
  directories are never modified by FRAG.
- Source identity markers beside each index so changing source tier forces a
  full reconciliation before delta sync resumes.
- Search/resolve results now report the selected source and source path.

### Security
- The indexer no longer follows symlinks and only accepts regular files whose
  resolved location remains inside the selected repository root. This blocks
  repository symlinks from exposing unrelated local host files.
- `git archive` materialization accepts only regular files/directories and
  drops symlinks, hardlinks, and device entries.
- Local origin metadata is read directly from git config text rather than by
  invoking `git config`, so config include directives are not evaluated merely
  to infer repository owner identity.

### Changed
- Live working clones are indexed from their current filesystem state without
  pulling first, so dirty and untracked development changes are searchable.
- `frag_status` can resolve local/index identity without requiring GitHub or
  Gitea credentials.

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
  path from a clean Python environment with `PIP_NO_INDEX=1`.
- GitHub CI now gates both `latest` and `stable`, and uses current v7 releases
  of `actions/checkout` and `actions/setup-python`.
- MCP search/resolve calls close their SQLite stores after each request.
- Plugin license metadata now matches the repository's GPLv3 license.

## [0.1.0]

Initial release.

### Added
- Two-stage retrieval: FTS5 candidate generation, then optional cosine
  re-rank when an embedder is configured.
- Dual-host support for GitHub and Gitea via a `HostProvider` plugin slot,
  with reference grammar `host[/owner]/repo`.
- Per-repo SQLite store.
- Content firewall for unsafe/non-source content.
- Delta sync driven by git changed paths.
- MCP stdio server exposing `frag_search`, `frag_resolve`, and `frag_status`.
- Claude Code plugin packaging and retrieval skill.
