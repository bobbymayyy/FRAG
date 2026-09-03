# FRAG Project State

Single source of truth for the current architecture.

## 1. Purpose

FRAG is a Claude Code plugin for minimal-context repository retrieval. Given a
`host[/owner]/repo` reference and a symptom/question, it chooses a source,
updates a per-repository index, and returns the smallest useful fragments.

Supported repository identities remain GitHub and Gitea. Repository bytes can
now come from the local repository hub, its mirrors/archive, or the remote
host.

## 2. Source acquisition

`resolve()` is local-first by default:

```text
parse ref
  -> source=auto
       -> /srv/repos/<host>/<repo> working clone
       -> /srv/repos/mirrors/.../<repo>.git
       -> newest /srv/repos/archive/*.tar.zst containing the mirror
       -> GitHub/Gitea remote provider
  -> reconcile source against per-repo index
  -> return RepoHandle + selected source
```

Explicit source modes:

- `auto`
- `worktree`
- `mirror`
- `archive`
- `remote`

`FRAG_REPO_HUB` overrides the hub root. When unset, `/srv/repos` is used if it
exists. Claude Code exposes the same setting as optional `repo_hub` userConfig.

### Hub mutation rule

FRAG does not write into managed hub inputs:

- `github/`: read only from FRAG's perspective
- `gitea/`: read only from FRAG's perspective
- `mirrors/`: never modified
- `archive/`: never modified

Mirror/archive source trees are materialized beneath `FRAG_HOME/materialized`.
Archive bare repos are first extracted into a temporary directory, then
materialized with `git archive`.

### Live worktrees

A working clone is indexed exactly as it exists on disk. FRAG does not fetch or
pull it. Dirty and untracked files are intentionally visible. Worktree source
uses a full file walk each request so those changes are noticed; unchanged
files still content-hash skip re-chunking.

### Mirrors and archives

Bare mirrors are discovered with bounded traversal that prunes `.git` object
trees. Owner/host identity is inferred from `remote.origin.url` by parsing the
config file directly rather than invoking `git config`.

Archive lookup scans snapshots newest-first for `<repo>.git/HEAD`, validates
candidate owner/host from the archived config, extracts only the selected bare
repository, and materializes HEAD into FRAG data.

## 3. Index/source consistency

One repository remains one SQLite file:

```text
${FRAG_HOME}/index/<host>/<owner>/<repo>.sqlite
```

A sibling `.source` marker records the current source identity. Switching from
one source tier to another forces a full reconciliation before delta sync can
resume. Changed-path deltas from one tree are never applied blindly to a
different tree.

Remote fallback clones remain under:

```text
${FRAG_HOME}/clones/<host>/<owner>/<repo>/
```

Materialized local sources live under:

```text
${FRAG_HOME}/materialized/mirror/...
${FRAG_HOME}/materialized/archive/...
```

## 4. Local filesystem safety

The indexer must only read regular files physically contained inside the
selected repository root.

Current invariants:

- file symlinks are never indexed;
- resolved file paths must remain inside the repository root;
- `git archive` materialization omits symlinks, hardlinks, and device entries;
- archive member paths are checked for absolute/traversal components;
- the existing content firewall still rejects binary/oversized/denied data.

This is required because local-hub access otherwise turns a repository symlink
into a possible read of unrelated host paths such as `/srv/repos/secrets`.

## 5. MCP/runtime

The MCP server is dependency-free at baseline and implements the small stdio
surface directly. It remains on the 2025-era lifecycle and returns `-32601` to
newer `server/discover` probes so clients can use the legacy initialize path.

Tools:

- `frag_search(query, ref?, top_k=8, source="auto")`
- `frag_resolve(ref, force_full_resync=false, source="auto")`
- `frag_status(ref)`

Search/resolve output includes `source` and `source_path`. Status reports the
last indexed source without syncing or touching the network.

`frag_status` resolves local/index identity without constructing a credentialed
GitHub/Gitea provider.

## 6. Launcher and configuration

`plugins/frag/scripts/frag-server` remains stdlib-only and performs no package
bootstrap. It maps Claude plugin options into:

- `FRAG_REPO_HUB`
- `FRAG_GITHUB_TOKEN`
- `FRAG_GITHUB_DEFAULT_OWNER`
- `FRAG_GITEA_URL`
- `FRAG_GITEA_TOKEN`
- `FRAG_GITEA_DEFAULT_OWNER`

Local worktree operation therefore does not require GitHub/Gitea credentials.
Remote fallback still validates provider credentials when constructed.

`FRAG_HOME` remains `${CLAUDE_PLUGIN_DATA}/home` so indexes/materialized trees
survive plugin updates.

## 7. Retrieval/storage invariants

- One repository = one SQLite store.
- `chunks_fts` is ordinary FTS5, not contentless, so eviction uses normal deletes.
- `paths=[]` means match nothing.
- Embedding model+dimension fingerprint prevents mixing incompatible vectors.
- Baseline retrieval is lexical-only with no required PyPI dependencies.
- Git clone/fetch error rendering redacts credential-bearing URL userinfo.

## 8. Distribution and CI

Repository default branch: `latest`. Release-oriented track: `stable`.

The plugin manifest deliberately omits semver so git commit SHA drives
marketplace updates. `pyproject.toml` bookkeeping version is currently `0.2.0`.

CI for both tracks runs:

1. Python tests;
2. the actual marketplace launcher from a clean offline Python environment;
3. MCP modern-probe fallback + initialize/tools-list handshake;
4. canonical plugin validation;
5. canonical marketplace validation;
6. ShellCheck for the installer.

The local-source suite additionally covers credential-free worktree use,
explicit remote bypass, local status, symlink escape prevention, bare mirror
materialization, and tar.zst archive materialization when zstd is present.

## 9. Open hardening / efficiency items

1. Remote provider tokens are still transiently present in git subprocess argv.
   Errors are redacted and credentials are not persisted, but argv transport
   should eventually be replaced with a credential-helper/askpass mechanism.
2. Live local worktrees currently full-walk on every retrieval. Content hashes
   avoid re-chunking, but a future local git-status/head fingerprint can reduce
   filesystem traversal while preserving dirty/untracked correctness.
3. Archive lookup lists snapshots on demand. A small archive catalog under
   `FRAG_HOME` could make repeated historical lookups cheaper.
4. Hub layout currently assumes live clones at `<hub>/github/<repo>` and
   `<hub>/gitea/<repo>`. Owner-nested live clone layouts can be added if needed.
