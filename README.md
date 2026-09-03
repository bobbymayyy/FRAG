# FRAG plugin marketplace

Claude Code plugin that retrieves the smallest useful set of source fragments
for a repository question. FRAG is local-first when a repository hub is
available and falls back to GitHub/Gitea only when it needs to.

## Install

```bash
claude plugin marketplace add bobbymayyy/FRAG
claude plugin install frag@frag
```

The repository default branch is `latest`. Pin the release-oriented track with:

```bash
claude plugin marketplace add bobbymayyy/FRAG@stable
```

A Gitea/full-URL marketplace uses `#<ref>` instead of GitHub shorthand's
`@<ref>`.

FRAG requires Python 3.11+ and `git`. Baseline MCP startup is stdlib-only: no
venv creation, pip install, FastMCP, or first-run network dependency download.

## Local-first repository hub

FRAG automatically recognizes `/srv/repos` when it exists. Another root can be
set through the optional `repo_hub` plugin option (`FRAG_REPO_HUB` for direct
runs).

Expected hub layout:

```text
/srv/repos/
  github/     live GitHub working clones
  gitea/      live Gitea working clones
  mirrors/    managed bare *.git mirrors
  archive/    managed *.tar.zst snapshots of mirrors
```

The default `source="auto"` order is:

1. **worktree** — use `/srv/repos/github/<repo>` or `/srv/repos/gitea/<repo>` exactly as it exists now;
2. **mirror** — locate a matching bare mirror and materialize its HEAD under FRAG's own data directory;
3. **archive** — locate the newest matching `tar.zst` snapshot, extract only its bare mirror to a temporary directory, then materialize HEAD under FRAG data;
4. **remote** — use the existing GitHub/Gitea provider and credentials.

`frag_search` and `frag_resolve` can force any tier with:

```text
source = auto | worktree | mirror | archive | remote
```

Examples:

```text
frag_search(ref="github/CERBERUS-2.0", query="intermittent login 500s")
frag_search(ref="github/STOKER", source="archive", query="old parser behavior")
frag_resolve(ref="gitea/AL3X", source="worktree")
```

### What FRAG modifies

FRAG treats the repository hub as input:

- `github/` and `gitea/`: **read only from FRAG's perspective**;
- `mirrors/`: **never modified**;
- `archive/`: **never modified**;
- materialized mirror/archive trees, source markers, indexes, and remote-fallback clones live under `FRAG_HOME` / `${CLAUDE_PLUGIN_DATA}`.

A live working clone is deliberately **not pulled first**. Dirty and untracked
files participate in retrieval, which makes FRAG useful while development is
in progress. Local worktrees are full-walk reconciled on each call, but
unchanged files are content-hash skipped rather than re-chunked.

Bare mirrors and archive snapshots are converted to source with `git archive`.
FRAG's tar reader accepts only regular files/directories and drops symlinks,
hardlinks, and device entries.

## Local filesystem safety

The indexer never follows repository symlinks. Only regular files whose
resolved path remains inside the selected repository root can be indexed.
This prevents a repository symlink from turning local-hub access into a read
primitive for unrelated paths such as `/srv/repos/secrets`.

The normal content firewall still rejects denied paths/extensions, binary
magic, NUL-containing data, invalid UTF-8, oversized files, and high-entropy
binary-like content.

## MCP tools

- `frag_search(query, ref?, top_k=8, source="auto")` — acquire/sync/index and return ranked fragments.
- `frag_resolve(ref, force_full_resync=false, source="auto")` — acquire/sync/index without searching.
- `frag_status(ref)` — inspect the existing index and report its last source without syncing or touching the network.

Results include `source` and `source_path`, so callers can tell whether the
answer came from a live clone, mirror, archive, or remote fallback.

## User configuration

The plugin declares:

- `repo_hub` — optional; blank auto-detects `/srv/repos`;
- `github_token`, `github_default_owner`;
- `gitea_url`, `gitea_token`, `gitea_default_owner`.

The token fields are sensitive. Claude Code stores them in credential storage
and exports plugin options as `CLAUDE_PLUGIN_OPTION_<KEY>`; `frag-server`
normalizes those after process spawn. Leaving every option blank does not stop
the MCP server from connecting, and local worktree retrieval can operate with
no GitHub/Gitea token at all.

A local clone's `remote.origin.url` is read directly from `.git/config` (not
through `git config`, so include directives are not evaluated) to infer owner
identity when a reference such as `github/FRAG` omits it.

## Persistent data and source switching

`${CLAUDE_PLUGIN_DATA}` persists across plugin updates. FRAG stores indexes,
remote-fallback clones, and materialized mirror/archive trees beneath:

```text
FRAG_HOME=${CLAUDE_PLUGIN_DATA}/home
```

One repository remains one SQLite index. A small source marker sits beside the
index. If a repo changes source tier, for example from a dirty local worktree
to `source="remote"`, FRAG forces a full index reconciliation before allowing
delta sync again. Delta paths from one tree are never applied blindly to
another.

## MCP startup and sandboxing

Claude Code starts:

```text
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/frag-server
```

The launcher prepends bundled `src/` to `PYTHONPATH` and execs the MCP module
with the same Python interpreter. It does not invoke pip. stdout remains MCP
JSON-RPC only; launcher diagnostics go to stderr.

FRAG intentionally implements the 2025-era MCP lifecycle. A newer client that
probes `server/discover` receives `-32601 Method not found`, then can fall back
to the supported `initialize` flow.

## Versioning and CI

`plugin.json` deliberately omits `version`; git commit SHA is the effective
marketplace version. The Python package version is bookkeeping only.

Both `latest` and `stable` are CI-gated. The workflow runs Python tests, the
real marketplace launcher in a clean offline runtime, canonical plugin and
marketplace validation, and ShellCheck for the installer.

Claude's validator warns when plugin semver is absent even for commit-SHA git
marketplaces, so CI uses normal validation rather than `--strict`; pytest
separately enforces FRAG's manifest invariants.

## Local development

```bash
cd plugins/frag/src
python -m pip install -e '.[dev]'
pytest -q
python handshake_check.py

cd ../../..
claude plugin validate ./plugins/frag
claude plugin validate .
```

To test against a nonstandard local hub:

```bash
FRAG_REPO_HUB=/path/to/repos FRAG_HOME=/tmp/frag-test \
  python -m frag.mcp_server
```

## Troubleshooting

```bash
claude mcp list
claude --debug mcp
```

If auto source selection chooses something unexpected, force `source` to
`worktree`, `mirror`, `archive`, or `remote`; FRAG returns the selected source
and path in every resolve/search result.
