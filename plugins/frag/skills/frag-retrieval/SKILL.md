---
name: frag-retrieval
description: "Use when investigating a bug, regression, or unexpected behavior in a GitHub or Gitea repository, especially when the user names a repo like github/CERBERUS-2.0 or gitea/AL3X. FRAG prefers an existing local repository hub, mirrors, and archives before remote cloning and retrieves only the relevant code fragments."
---

# Finding relevant code with FRAG

FRAG answers one question well: *given a symptom or technical question, which
fragments of this repository are worth looking at?* It exists so that
investigating a large repo does not mean pushing the whole codebase into
context.

## Source selection

`frag_search` and `frag_resolve` accept a `source` option:

- `auto` (default): working clone -> bare mirror -> newest archive -> remote
- `worktree`: require the live working clone from the repository hub
- `mirror`: require a local bare mirror; FRAG materializes HEAD into its own data directory
- `archive`: require the newest matching `tar.zst` mirror snapshot
- `remote`: bypass local sources and use GitHub/Gitea

When `/srv/repos` exists, FRAG discovers it automatically. Another hub can be
configured with the plugin's `repo_hub` option.

Prefer the default `auto` unless the user asks for a particular source or the
question itself is source-specific, such as "what did the archived copy have?"

A live working clone is read as it exists on disk. FRAG does not pull it
first, so dirty and untracked development changes participate in retrieval.
Managed `mirrors/` and `archive/` inputs are never modified.

## When to use it

Use `frag_search` when the user describes a problem and names a repo in
`host[/owner]/repo` form:

- "Login is returning 500s intermittently in github/CERBERUS-2.0"
- "gitea/AL3X is retrying forever when the registry is down"
- "Check the archived github/STOKER snapshot for the old parser"

If the code is already the current working directory and you know the files
you need, ordinary Read/Glob/Grep remains cheaper. FRAG is useful when the
question is retrieval-shaped: you need it to narrow a repository before you
know what to open.

## How to call it

Pass the user's own description as `query` and the repo as `ref`:

```
frag_search(
  ref="github/CERBERUS-2.0",
  query="login returns intermittent 500s under load, need graceful degradation"
)
```

To force a historical snapshot:

```
frag_search(
  ref="github/STOKER",
  source="archive",
  query="old parser behavior before the current implementation"
)
```

**Pass the symptom, not just keywords.** FRAG performs lexical retrieval and
can optionally semantic re-rank. A full description carries more signal than
a single noun.

**Pass `ref` explicitly when you know it.** Free-text reference extraction is
a fallback, not a reason to omit a known repo reference.

## Reading the results

Results include the selected `source` and `source_path`, followed by fragments
with `path`, `start_line`, `end_line`, `text`, and `score`. Treat fragments as
candidates rather than diagnoses.

1. Inspect the highest-ranked fragments and reason about which are implicated.
2. If a relevant fragment needs surrounding context, refine the query or open the named file.
3. If nothing useful appears, reformulate once with different vocabulary. Then report that retrieval did not surface a useful candidate rather than guessing.

## The other tools

- `frag_resolve(ref, source="auto")` acquires the selected source and updates its index without searching.
- `frag_status(ref)` reports the existing index and its last source without syncing or touching the network.

Local working trees are full-walk reconciled so dirty/untracked edits are
noticed, but unchanged files are content-hash skipped and not re-chunked.
