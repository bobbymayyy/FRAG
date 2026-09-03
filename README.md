# FRAG plugin marketplace

Claude Code plugin that bundles the FRAG MCP server and a retrieval skill.
FRAG indexes GitHub and Gitea repositories and returns only the source
fragments relevant to a symptom or question instead of pushing an entire
codebase into the model context.

## Install

From the GitHub marketplace:

```bash
claude plugin marketplace add bobbymayyy/FRAG
claude plugin install frag@frag
```

The repository default branch is `latest`, so the shorthand above follows
that track. To pin a branch or tag, add a ref:

```bash
claude plugin marketplace add bobbymayyy/FRAG@stable
```

For a Gitea mirror or another full git URL, use the URL form and `#<ref>`:

```bash
claude plugin marketplace add https://gitea.example.com/<owner>/FRAG.git#stable
claude plugin install frag@frag
```

### Or use the installer

`install.sh` wraps marketplace add/update plus plugin install and performs a
small prerequisite check. It is idempotent, so running it again updates the
registered marketplace rather than failing because it already exists.

```bash
git clone https://github.com/bobbymayyy/FRAG.git
cd FRAG
./install.sh
```

Options include `--ref <branch-or-tag>`, `--source gitea`, `--url <git-url>`,
`--scope user|project|local`, and `--uninstall`.

FRAG requires Python 3.11 or newer and the `git` CLI. It does **not** require
`python3-venv`, pip, FastMCP, or a first-run dependency download.

## MCP startup and sandboxing

The MCP server is intentionally dependency-free at baseline. Claude Code
starts:

```text
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/frag-server
```

The launcher prepends the plugin's bundled `src/` directory to `PYTHONPATH`
and execs `frag.mcp_server` with the same interpreter. It does not create a
venv and it never invokes pip. That matters when Claude Code is running with
bubblewrap or another network-restricted sandbox: the MCP server can come
online without contacting PyPI.

`scripts/frag-server` also keeps stdout completely reserved for MCP JSON-RPC.
Any launcher diagnostics go to stderr so they cannot corrupt the protocol.

The server implements the small stdio MCP surface FRAG needs directly:
`initialize`, `ping`, `tools/list`, and `tools/call`. The advertised tools are:

- `frag_search` - sync/index a repo and return the most relevant fragments
- `frag_resolve` - sync/index without searching
- `frag_status` - inspect an existing local index without a network sync

## User configuration

The plugin declares these `userConfig` values:

- `github_token`
- `github_default_owner`
- `gitea_url`
- `gitea_token`
- `gitea_default_owner`

The two token fields are marked `sensitive`, so Claude Code stores them in its
credential storage instead of plaintext plugin settings.

The MCP launch configuration itself does **not** interpolate
`${user_config.*}` values. Claude Code exports configured plugin options as
`CLAUDE_PLUGIN_OPTION_<KEY>` environment variables, and `frag-server` maps
those into FRAG's internal environment after the process has already spawned.
This makes configuration independent from process startup: leaving all five
values blank does not prevent the MCP server from connecting. A host operation
that needs missing credentials fails when that operation is called instead.

For example, after install this should report a connected server even before
host credentials are configured:

```bash
claude mcp list
```

## Persistent data

`${CLAUDE_PLUGIN_ROOT}` is version-scoped and changes when the plugin updates.
`${CLAUDE_PLUGIN_DATA}` persists across updates, so `.mcp.json` sets:

```text
FRAG_HOME=${CLAUDE_PLUGIN_DATA}/home
```

Repository clones and SQLite indexes therefore survive plugin updates. The
runtime source remains in the versioned plugin cache; only state belongs in
the persistent data directory.

## Why a plugin and not only a skill

A skill is Markdown guidance. It can teach Claude when and how to retrieve
code, but it cannot expose tools. FRAG needs a running MCP process for
`frag_search`, `frag_resolve`, and `frag_status`.

The plugin is the container for both pieces:

```text
.claude-plugin/marketplace.json     marketplace catalog
plugins/frag/
  .claude-plugin/plugin.json        manifest + userConfig
  .mcp.json                         MCP server registration
  scripts/frag-server               dependency-free launcher
  skills/frag-retrieval/SKILL.md    retrieval guidance
  src/                              FRAG Python package + tests
```

## Versioning and branches

`plugins/frag/.claude-plugin/plugin.json` deliberately omits a `version`
field. Claude Code can therefore resolve the effective plugin version from
the git commit SHA instead of waiting for a manually bumped manifest version.
The Python package version in `pyproject.toml` is only human bookkeeping.

Both `latest` and `stable` are CI-gated. `latest` is the repository default
and development track; `stable` can be explicitly pinned for a release-oriented
install. A pull request into either branch runs the same Python tests,
marketplace-launch MCP handshake, and strict plugin/marketplace validation.

## CI startup regression test

The MCP smoke test intentionally exercises the production launch path instead
of importing `frag.mcp_server` directly:

1. CI creates a brand-new Python venv.
2. Nothing from FRAG or an MCP framework is installed into it.
3. `PIP_NO_INDEX=1` disables package downloads.
4. The test starts `plugins/frag/scripts/frag-server`.
5. It completes MCP `initialize` and `tools/list` and verifies all three tools.

That catches failures in the launcher, Python path setup, protocol framing,
unconfigured `userConfig`, and accidental runtime dependency additions.

## Local development

```bash
# Load directly without installing
claude --plugin-dir ./plugins/frag

# Python tests
cd plugins/frag/src
python -m pip install -e '.[dev]'
pytest -q
python handshake_check.py

# Plugin validation
cd ../../..
claude plugin validate ./plugins/frag --strict
claude plugin validate . --strict
```

Changes to `.mcp.json`, `scripts/`, or `src/` require a plugin reload or a new
Claude Code session. Editing the skill content itself can be picked up without
rebuilding a Python environment because there is no runtime package install.

## Troubleshooting

If the server shows failed:

```bash
claude mcp list
claude --debug mcp
```

`frag-server` writes fatal launcher errors to stderr with a `[frag-server]`
prefix. Once the server connects, repository-specific authentication or clone
errors are tool-call errors rather than MCP startup failures.
