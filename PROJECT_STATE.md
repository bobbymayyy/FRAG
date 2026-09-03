# FRAG — Project State

Single source of truth. Read this first when resuming; update it at the end
of each session.

This is a **current-state** document, not a log. Superseded approaches live
in one clearly marked section at the bottom so they can't be mistaken for how
things work now.

---

## 1. What FRAG is

Given a free-text symptom report ("noticing X behavior, need Y instead") and
a repository reference, FRAG returns the smallest set of code fragments worth
looking at. It exists so investigating a bug in a large repo doesn't mean
pushing the whole codebase through the context window.

It targets a **dual-git-host environment**: GitHub and Gitea, both read/write.

It ships as a **Claude Code plugin**, installed from a git-hosted marketplace.

---

## 2. Repository layout

```
FRAG/                                       ← the git repo (GitHub-hosted)
├── .claude-plugin/marketplace.json         catalog
├── .github/workflows/validate-stable.yml   CI (primary)
├── .gitea/workflows/validate-stable.yml    CI (mirror)
├── install.sh                              optional installer
├── README.md  CHANGELOG.md  LICENSE
├── .gitignore  .gitattributes
└── plugins/frag/
    ├── .claude-plugin/plugin.json          manifest + userConfig prompts
    ├── .mcp.json                           MCP server registration
    ├── scripts/frag-server                 ensure venv, exec server
    ├── skills/frag-retrieval/SKILL.md      when to use frag_search
    └── src/
        ├── pyproject.toml
        ├── handshake_check.py
        ├── frag/                           library + MCP server
        └── tests/                          test_core.py, test_manifests.py
```

### Three independent names

| Name | Set in | Value | Tied to repo? |
|---|---|---|---|
| Git repo | GitHub/Gitea; `GITHUB_REPO` in `install.sh` | `FRAG` | yes |
| Marketplace | `.claude-plugin/marketplace.json` → `name` | `frag` | no |
| Plugin | `plugins/frag/.claude-plugin/plugin.json` → `name` | `frag` | no |

`claude plugin install frag@frag` is `plugin@marketplace`. Neither half is
the repo name. If you rename the *plugin* later, use the catalog's `renames`
field so existing installs migrate instead of vanishing.

---

## 3. Architecture

`resolve()` is the single entry point: parse ref → sync worktree → sync index
→ return a `RepoHandle`.

| Module | Responsibility |
|---|---|
| `registry.py` | Priority-based plugin registry; `REGISTRY.best(slot)` returns the highest-priority plugin that actually constructs |
| `hosts/base.py` | `HostProvider` protocol, `RepoRef`, ref-grammar parsing |
| `hosts/github.py`, `hosts/gitea.py` | The two host plugins |
| `hosts/gitutil.py` | Clone-or-pull; reports `changed_paths` for delta sync |
| `firewall.py` | Content firewall, run on every touched file every sync |
| `chunker.py` | Dependency-free line-window chunker (baseline) |
| `embedder.py` | Embedder protocol; nothing registered by default |
| `store.py` | Per-repo SQLite: `files`, `chunks`, `chunks_fts`, `vectors`, `meta` |
| `indexer.py` | Firewall → chunk → store; full and delta sync; eviction |
| `retriever.py` | Two-stage search |
| `resolve.py` | Orchestration |
| `mcp_server.py` | FastMCP stdio server |

### Core principles

- **No config file.** Inputs are the reference grammar and environment
  variables only. Modularity is a development seam, not a settings surface.
- **Eager validation.** Plugins validate dependencies and credentials in
  `__init__`. A plugin that can't do its job must raise at construction, not
  degrade silently while reporting itself active. Silent wrong answers are
  the worst failure class.
- **Scope by construction.** One repo is one SQLite file. There's no shared
  table for a scoped query to leak out of.
- **Exact invariants in tests**, each with a negative control proving it
  fails against a broken tree.

### Non-obvious implementation facts

- **`chunks_fts` is a plain FTS5 table, not contentless.** Contentless tables
  reject ordinary `DELETE ... WHERE rowid`, which breaks eviction. Small
  storage duplication traded for correct deletes.
- **`paths=[]` means "match nothing", not "no filter".** An empty scope is a
  real empty scope.
- **Embedding fingerprint** (model name + dim) is stored in `meta`. On
  mismatch, sync degrades to lexical-only rather than mixing incompatible
  vector spaces.
- **`FRAG_HOME` must live in `${CLAUDE_PLUGIN_DATA}`.** `${CLAUDE_PLUGIN_ROOT}`
  is version-scoped and swept ~14 days after an update; indexes stored there
  would be wiped by every push to `stable`.
- **The MCP server launches as `python3 <script>`**, so a lost executable bit
  can't break the install.
- **`plugin.json` must contain only recognized fields.** CI runs
  `claude plugin validate --strict`, which treats unrecognized keys as
  errors — so no hand-written comment keys.

---

## 4. Distribution

Push to `stable` → next Claude Code session runs the new code.

This works because `plugin.json` **deliberately omits `version`**. Claude Code
then resolves the version from the git commit SHA, so every push is a new
version. Setting an explicit `version` would silently switch to manual-bump
updates and break push-to-deploy. `test_plugin_manifest_has_no_version_field`
guards this.

The trade-off: there is no publish step that could fail safely. Whatever
lands on `stable` reaches sessions directly, which makes the CI gate the only
thing between a bad commit and every install. It runs unit tests, a real MCP
handshake (`initialize` + `tools/list`), and strict manifest validation.

### Install

```bash
claude plugin marketplace add <owner>/FRAG
claude plugin install frag@frag
```

Or `./install.sh`, which adds preflight checks (git, claude CLI, python3
>= 3.11, the `venv` module), idempotent re-runs, `--ref` pinning,
`--source gitea`, and `--uninstall`. It never accepts tokens as arguments —
those would land in shell history and the process list.

### Secrets

Tokens are declared in `plugin.json` under `userConfig` with
`sensitive: true`, so Claude Code prompts at enable time and stores them in
the OS keychain rather than plaintext `settings.json`. Keychain storage
shares a ~2KB budget with OAuth tokens; two PATs fit comfortably.

### Rollback

No per-machine version pin. To roll back, push a revert commit, or add the
marketplace pinned to a tag. **The two pinning syntaxes differ:** GitHub
shorthand uses `owner/repo@<tag>`, a full git URL uses `...git#<tag>`.
Getting them backwards silently tracks the default branch instead of the tag.

---

## 5. Verification status

### Verified in sandbox
- Firewall rejects binaries, accepts source
- Unchanged files skip re-chunking (content-hash comparison)
- Deleting a file evicts it from search results
- `paths=[]` returns nothing; `paths=['x']` returns only that file
- Delta sync touches only changed paths
- Ref grammar: explicit and free-text; unknown host returns `None`
- Eager validation: missing token → construction raises → registry skips;
  missing owner with no default → `ValueError`
- All 11 manifest tests pass, with 5 negative controls confirmed failing
  against a deliberately broken tree
- Installer: 9 paths against a stubbed CLI (fresh, idempotent, both pinning
  forms, gitea source, uninstall, every error path)
- Plugin source fingerprint is deterministic and changes when source changes

### NOT verified — no network egress in the build environment
- Any real `git clone`/`fetch` against GitHub or Gitea
- The plugin's `pip install` of its bundled source (the offline attempt
  failed only at build-dependency download, the expected offline failure)
- `claude plugin validate`, `marketplace add`, and the enable-time
  `userConfig` prompt — all installer testing used a stub
- `shellcheck install.sh` (not installable offline); that CI step is unproven
- Gitea Actions `act_runner` availability

---

## 6. Open gaps

1. **`GITHUB_REPO` in `install.sh` is still `YOUR-GH-OWNER/FRAG`.** Set it
   before publishing.
2. **`LICENSE` says "Nathan"** — set the full legal name if that matters.
3. **First live run is the real test.** Clone a small repo from both hosts
   and confirm `changed_paths` diffing behaves across real pulls.
4. **No embedder registered.** Only lexical retrieval is exercised beyond
   unit level; the `semantic` extra is unbuilt.
5. **Marketplace-present detection greps human-readable CLI output**, which
   could change format. `claude plugin list --json --available` would be
   sturdier but needs verification first.
6. **Rename detection in delta sync** — a rename appears as delete+add, which
   is handled correctly but wastes a re-chunk. `git diff -M` would detect it.
7. **`gitutil` syncs only the default branch.** It uses `git fetch <url> HEAD`
   plus `merge --ff-only` so the token never touches `.git/config`. A
   PR-branch workflow would need a branch parameter threaded through.
8. **No CLI** for out-of-band operation; only the MCP surface exists.
9. **`frag_status` requires a resolvable owner** even to read already-indexed
   data. Minor rough edge.
10. **CI assumes Node on the runner** for `npx @anthropic-ai/claude-code`.
    Fine on `ubuntu-latest`; check if the Gitea runner image is slimmer.
11. **Decide whether `stable` or `main` is the default branch.** Marketplace
    shorthand tracks the default; the CI workflow triggers on both.

---

## 7. Retired approaches — do not resurrect without reading this

### Gitea PyPI registry + `frag-launch` shim
An earlier design published wheels to Gitea's package registry with
auto-derived `X.Y.<commit-count>` versions, and shipped a `frag-launch` shim
that polled the registry, built versioned venvs, ran a handshake self-check,
and flipped a symlink.

**Superseded by plugin distribution**, which does the same job with none of
that machinery: commit-SHA versioning replaces the version derivation,
`scripts/frag-server` replaces the shim, and `userConfig` keychain storage
replaces plaintext tokens in the MCP config.

Two things the retired design had that the current one doesn't, if they ever
matter enough to revisit: a handshake self-check *before* a version goes live
on a given machine, and per-machine pinning via `FRAG_PINNED_VERSION`.

### A skill as the distribution container
Considered and rejected. Skills are Markdown instruction files and cannot
provide tools; FRAG is an MCP server. A plugin holds both. The skill still
exists — as a component *inside* the plugin.

### A separate "triage" signal engine
Briefly scoped as heuristic detection of suspect code (lexical markers, git
churn). Dropped: the user's symptom report *is* the query, and two-stage
retrieval against it already produces the minimal fragment set. No separate
engine was built.

### less-toil / Repository Cognition Engine
Read once as source material. Not a target, dependency, or deliverable. Do
not audit, modify, or package it.
