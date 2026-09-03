# FRAG plugin marketplace

Claude Code plugin that bundles the FRAG MCP server and a retrieval skill.

## Install

Two commands, no script needed:

```bash
# From GitHub (shorthand; pin with @tag)
claude plugin marketplace add <owner>/FRAG
claude plugin install frag@frag

# From Gitea (full git URL; pin with #tag)
claude plugin marketplace add https://gitea.example.com/<owner>/FRAG.git
claude plugin install frag@frag
```

### Or use the installer

`install.sh` wraps those two commands and adds preflight checks, so a missing
`python3-venv` or an unsupported Python version fails with a clear message
instead of halfway through the plugin's first run. It's idempotent, so
re-running it updates rather than erroring.

```bash
git clone https://github.com/<owner>/FRAG.git
cd FRAG
./install.sh
```

Options: `--ref v1.2.0` to pin, `--source gitea` for the mirror, `--url` for
an explicit repo, `--scope project` to share via the repo's settings, and
`--uninstall`.

The script never accepts tokens as arguments. Passing a PAT on a command line
puts it in shell history and the process list; Claude Code prompts for the
sensitive fields at enable time and stores them in the OS keychain instead.

**On `curl | bash`:** the installer is short and readable, and cloning the
repo first (as above) lets you read it before running it. If you'd rather
pipe it straight from a raw URL, pin to a tag rather than a branch so the
bytes you audited are the bytes you run.

To pin to a branch or tag, append `#<ref>` to the git URL:

```bash
claude plugin marketplace add https://gitea.example.com/<owner>/FRAG.git#stable
```

On enable, Claude Code prompts for the tokens declared in `userConfig`. The
two marked `sensitive` go to the OS keychain (or `~/.claude/.credentials.json`
where no keychain exists), not into `settings.json` in plaintext.

The first session after install builds a Python venv in the plugin's
persistent data directory and installs the bundled source into it. That takes
a few seconds; subsequent sessions skip it.

## Why a plugin and not a skill

A skill is a Markdown instruction file. It can tell Claude *how* to approach
something, but it cannot provide tools. FRAG is an MCP server — it needs to
run as a process and expose `frag_search`, `frag_resolve`, and `frag_status`.

A plugin is the container that holds both: it declares the MCP server in
`.mcp.json` *and* ships `skills/frag-retrieval/SKILL.md`, which teaches
Claude when to reach for `frag_search` instead of reading files wholesale.
The tools without the skill get ignored; the skill without the tools has
nothing to call.

## Why `version` is omitted from plugin.json

Deliberate. Claude Code resolves a plugin's version from the first source
that is set, and `plugin.json`'s `version` field is first in that order. If
it were set, users would only receive an update when the field was bumped by
hand — pushing new commits would have no effect and `/plugin update` would
report "already at the latest version".

With the field omitted, the version resolves from the source's **git commit
SHA**, so every push to `stable` is a new version. This is the documented
approach for internal plugins under active development, and it's what makes
"push to stable, get it next session" work without a publish step.

The trade-off: there is no publish step to fail safely. Whatever lands on
`stable` is what sessions pick up. That's why `.gitea/workflows/validate-stable.yml`
runs the full test suite, the MCP handshake check, and manifest validation —
that gate is the only thing between a bad commit and every session.

## Why the venv lives in the data directory

`${CLAUDE_PLUGIN_ROOT}` is version-scoped: it changes on every plugin update,
and the previous directory is swept roughly 14 days later. `${CLAUDE_PLUGIN_DATA}`
persists across updates.

This matters most for `FRAG_HOME`, which is set to `${CLAUDE_PLUGIN_DATA}/home`
in `.mcp.json`. Repo clones and SQLite indexes live there. Had they been left
under the plugin root, **every push to `stable` would wipe every repo index**
and force a full re-clone and re-index on the next session.

`scripts/frag-server` does the venv setup inline rather than from a
`SessionStart` hook, so there's no dependency on hook-vs-server startup
ordering — the venv is ready before the server starts because that script
*is* the server's start. It reinstalls only when a hash of the bundled source
changes, so the steady-state cost is one hash of a small tree.

## Layout

```
.claude-plugin/marketplace.json     catalog
plugins/frag/
  .claude-plugin/plugin.json        manifest + userConfig (token prompts)
  .mcp.json                         MCP server registration
  scripts/frag-server               ensure venv, exec server
  skills/frag-retrieval/SKILL.md    when to use frag_search
  src/                              the FRAG Python package + tests
```

## Local development

```bash
claude --plugin-dir ./plugins/frag      # load without installing
claude plugin validate ./plugins/frag --strict
claude plugin validate . --strict       # the catalog
/reload-plugins                          # after changing .mcp.json or scripts
```

Editing `SKILL.md` takes effect immediately in the current session. Changes
to `.mcp.json`, `scripts/`, or `src/` need `/reload-plugins` or a restart.

## Status

Verified in the build sandbox: all three manifests parse, the skill's
frontmatter parses with `name` and `description`, the source fingerprint is
deterministic and changes when source changes (so updates trigger a
reinstall), and `python -m venv` succeeds.

**Not verified** (no network egress in the build environment): the actual
`pip install` of the bundled source, `claude plugin validate`, adding the
marketplace from a live Gitea URL, and any real `git clone` against
GitHub/Gitea. The offline install attempt failed only at build-dependency
download, which is the expected offline failure and not a defect in the
mechanism.
