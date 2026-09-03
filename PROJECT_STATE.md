# FRAG Project State

Single source of truth for the current architecture. Read this first when
resuming work and update it when implementation decisions change.

This document describes how FRAG works now. Historical approaches are kept in
the final section so they are not mistaken for active architecture.

---

## 1. Purpose

FRAG is a Claude Code plugin for minimal-context repository retrieval. Given a
repository reference and a symptom/question, it synchronizes the repository,
indexes accepted source files, and returns the smallest useful set of code
fragments rather than loading an entire codebase into model context.

Supported repository hosts:

- GitHub
- Gitea

Current repository operations are read/sync/index operations. FRAG does not
write changes back to the remote repository.

The plugin ships two cooperating pieces:

1. an MCP server exposing retrieval tools;
2. a `frag-retrieval` skill teaching Claude when to call those tools.

---

## 2. Repository and plugin layout

```text
FRAG/
├── .claude-plugin/marketplace.json
├── .github/workflows/validate-stable.yml
├── .gitea/workflows/validate-stable.yml
├── README.md
├── CHANGELOG.md
├── PROJECT_STATE.md
├── LICENSE
├── install.sh
└── plugins/frag/
    ├── .claude-plugin/plugin.json
    ├── .mcp.json
    ├── scripts/frag-server
    ├── skills/frag-retrieval/SKILL.md
    └── src/
        ├── pyproject.toml
        ├── handshake_check.py
        ├── frag/
        │   ├── mcp_server.py
        │   ├── resolve.py
        │   ├── retriever.py
        │   ├── indexer.py
        │   ├── store.py
        │   ├── firewall.py
        │   ├── chunker.py
        │   ├── embedder.py
        │   ├── registry.py
        │   └── hosts/
        └── tests/
```

Independent names:

| Thing | Current value |
|---|---|
| Git repository | `bobbymayyy/FRAG` |
| Marketplace name | `frag` |
| Plugin name | `frag` |
| MCP server key | `frag` |

The scoped MCP server name shown by Claude Code is therefore
`plugin:frag:frag`.

---

## 3. Runtime architecture

### Resolve path

`resolve()` remains the single repository orchestration path:

```text
parse ref
  -> select host provider
  -> resolve owner/repo
  -> clone or fast-forward fetch
  -> determine changed paths
  -> firewall touched files
  -> chunk accepted files
  -> update per-repo SQLite index
  -> return RepoHandle
```

Core modules:

| Module | Responsibility |
|---|---|
| `registry.py` | priority-based implementation registry |
| `hosts/base.py` | provider protocol, `RepoRef`, reference parsing |
| `hosts/github.py` | GitHub URL/auth construction |
| `hosts/gitea.py` | Gitea URL/auth construction |
| `hosts/gitutil.py` | clone/fetch/fast-forward and changed-path calculation |
| `firewall.py` | content admission checks |
| `chunker.py` | dependency-free line-window chunking |
| `store.py` | one SQLite database per repository |
| `indexer.py` | full/delta synchronization into the store |
| `retriever.py` | lexical candidate retrieval and optional re-ranking |
| `resolve.py` | repository orchestration |
| `mcp_server.py` | dependency-free MCP stdio implementation |

### MCP server

FRAG no longer depends on FastMCP or the Python MCP SDK for its baseline
runtime. `mcp_server.py` implements only the protocol surface FRAG needs:

- `initialize`
- `ping`
- `tools/list`
- `tools/call`

Advertised tools:

- `frag_search`
- `frag_resolve`
- `frag_status`

Tool failures are returned as MCP tool errors instead of terminating the
server. Search/resolve calls close their SQLite store after each request so a
long-running MCP process does not accumulate connections.

### Marketplace launcher

`.mcp.json` starts:

```text
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/frag-server
```

`frag-server` is intentionally stdlib-only. It:

1. resolves plugin root/data paths;
2. maps configured Claude plugin options into FRAG environment variables;
3. prepends `${CLAUDE_PLUGIN_ROOT}/src` to `PYTHONPATH`;
4. execs `python -m frag.mcp_server`.

It does **not** create a venv, invoke pip, or contact PyPI. This is required
for reliable startup inside bubblewrap/network-restricted Claude Code
sandboxes.

stdout is reserved for MCP JSON-RPC. Launcher diagnostics use stderr.

### User configuration

`plugin.json` declares:

- `github_token` (sensitive)
- `github_default_owner`
- `gitea_url`
- `gitea_token` (sensitive)
- `gitea_default_owner`

`.mcp.json` deliberately contains no `${user_config.*}` substitutions.
Claude Code exports configured options as `CLAUDE_PLUGIN_OPTION_<KEY>` and the
launcher maps those values after process spawn. This means all five options
may be unset while the MCP server still connects.

GitHub and Gitea providers continue to validate the credentials they require
when a provider is actually constructed. Missing configuration therefore
becomes a tool-call/provider error, not an MCP startup failure.

### Persistent state

`FRAG_HOME` is `${CLAUDE_PLUGIN_DATA}/home`.

Repository state lives under:

```text
${FRAG_HOME}/clones/<host>/<owner>/<repo>/
${FRAG_HOME}/index/<host>/<owner>/<repo>.sqlite
```

One repository equals one SQLite file. Repository scope is therefore enforced
by which database is opened rather than a query predicate over shared data.

---

## 4. Retrieval and safety invariants

### Content firewall

Every touched file is re-evaluated before indexing. The firewall rejects
known binary patterns, NUL-containing data, invalid UTF-8, and content that
fails the configured heuristics.

### Delta synchronization

Existing repositories use fetch + fast-forward merge and compare the old/new
HEAD to identify changed paths. An unchanged repository yields an empty delta.
A fresh clone or forced resync performs a full index walk.

### FTS storage

`chunks_fts` is a normal FTS5 table, not a contentless FTS table. This is
intentional because ordinary row deletion is required for correct eviction.

`paths=[]` means match nothing. It must never be interpreted as an omitted
scope.

### Embeddings

The baseline runtime is lexical-only and has no required third-party Python
dependencies. Optional extras remain declared for semantic/symbol/ANN work,
but no production embedder is currently registered by default.

Embedding model name + dimension are stored as a fingerprint so incompatible
vector spaces are never silently mixed.

### Credential errors

Authenticated clone/fetch URLs currently contain token userinfo for the git
subprocess call. They are not persisted to `.git/config`.

`gitutil._run()` redacts URL userinfo from both command text and stderr before
raising an exception. This prevents a failed git command from returning a PAT
through an MCP tool error.

The token is still transiently visible in git process argv on the local host.
Moving authentication entirely out of argv is an open hardening item.

---

## 5. Distribution and CI

The repository default branch is `latest`.

Tracks:

- `latest`: default/development marketplace track
- `stable`: explicitly pinnable release track

`plugin.json` deliberately omits `version`, so plugin identity/update
resolution can use the source git commit SHA. `pyproject.toml` currently says
`0.1.1` for human bookkeeping only.

### GitHub Actions

The primary workflow runs for pushes and pull requests targeting **both**
`latest` and `stable`.

It performs:

1. Python 3.11 setup;
2. editable dev install;
3. unit tests;
4. clean-runtime marketplace MCP handshake;
5. strict plugin manifest validation;
6. strict marketplace validation;
7. shellcheck for `install.sh`.

The clean-runtime handshake is the key startup invariant. CI creates a new
venv with no FRAG/MCP packages installed, sets `PIP_NO_INDEX=1`, launches the
real `scripts/frag-server`, completes `initialize`, and verifies all three
FRAG tools are listed.

The GitHub-hosted workflow uses `actions/checkout@v7` and
`actions/setup-python@v7` so current runners do not have to force deprecated
Node 20 action bundles onto Node 24.

### Gitea Actions

The mirror workflow gates `latest` and `stable` with the same FRAG tests and
clean-runtime handshake. It intentionally keeps older action majors until
compatibility with the deployed `act_runner` is verified.

---

## 6. Current verification and open gaps

### Covered by tests/CI

- firewall accepts source and rejects representative binary data;
- deleted files are evicted;
- unchanged files are not re-chunked;
- delta sync only touches changed paths;
- empty path scope matches nothing;
- repository reference grammar;
- manifest/catalog structure;
- sensitive token metadata;
- no required baseline PyPI dependencies;
- launcher contains no pip/venv bootstrap;
- userConfig is not interpolated in `.mcp.json`;
- every declared option has a launcher mapping;
- git URL credentials are redacted from raised errors;
- production marketplace launcher completes MCP initialize/tools-list in a
  clean offline Python environment.

### Open gaps

1. **Live post-merge marketplace test.** Update/install FRAG from GitHub in a
   real Claude Code session and confirm `claude mcp list` reports
   `plugin:frag:frag` connected.
2. **Client userConfig UX.** Claude Code surfaces differ in when/how they show
   plugin configuration. Startup no longer depends on that UI working, but
   host operations still need their configured credentials.
3. **Git credentials in process argv.** Errors are redacted, but auth should
   eventually move to an askpass/header mechanism that keeps PATs out of git
   argv entirely.
4. **Public GitHub without a token.** `GitHubProvider` currently requires
   `FRAG_GITHUB_TOKEN` even for public repositories. Decide whether anonymous
   public clone should be a supported fallback.
5. **`frag_status` provider dependency.** Status currently constructs the host
   provider to resolve owner/default-owner, so missing host credentials can
   block an otherwise local status query.
6. **No production embedder registered.** Lexical retrieval is the only
   baseline runtime path.
7. **Default-branch-only sync.** `gitutil` fetches remote HEAD. PR/feature
   branch retrieval needs a branch/ref parameter threaded through the stack.
8. **Rename optimization.** Renames are currently handled correctly as
   delete+add but are re-chunked rather than detected with rename-aware diff.
9. **No standalone CLI.** FRAG is currently MCP-first.

---

## 7. Retired approaches

### FastMCP / Python MCP runtime dependency

The initial plugin depended on unrestricted `fastmcp` and imported
`mcp.server.fastmcp.FastMCP`. FastMCP 4.x resolved MCP 2.x, where that import
was removed, causing the server to exit at startup and Claude Code to report
`CONNECTION_CLOSED`.

Pinning the old SDK would have restored the import but would not have solved
the more important sandbox issue: first-run MCP startup still required a PyPI
download. The active design is therefore the small stdlib MCP implementation.

### First-run persistent venv bootstrap

The old `frag-server` created `${CLAUDE_PLUGIN_DATA}/venv` and ran pip against
the bundled source whenever its source fingerprint changed. This was removed
because plugin startup may run without PyPI/network access.

Persistent **data** still belongs in `CLAUDE_PLUGIN_DATA`; executable source
comes directly from the versioned plugin cache.

### Gitea PyPI registry + `frag-launch`

An earlier design published Python artifacts and used a launcher that polled a
package registry, created versioned venvs, handshook new versions, and flipped
a symlink. Claude plugin distribution superseded that machinery.

### Skill-only distribution

Rejected because skills cannot expose tools. The skill remains a component
inside the plugin, while the MCP server provides the callable surface.

### Separate triage signal engine

Dropped. The symptom/question itself is the retrieval query; the existing
retrieval pipeline already ranks relevant fragments without a second signal
engine.
