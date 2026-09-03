---
name: frag-retrieval
description: "Use when investigating a bug, regression, or unexpected behavior in a GitHub or Gitea repository that is not the current working directory - especially when the user describes a symptom ('seeing X, need Y instead') and names a repo like github/CERBERUS-2.0 or gitea/infra/deploy-tools. Retrieves only the code fragments relevant to the symptom instead of reading the whole repository."
---

# Finding relevant code with FRAG

FRAG answers one question well: *given a description of a symptom, which
fragments of this repository are worth looking at?* It exists so that
investigating a bug in a large repo doesn't mean pulling the whole codebase
into context.

## When to use it

Use `frag_search` when the user describes a problem in a repo that isn't
the current working directory, and names it in `host[/owner]/repo` form:

- "Login is returning 500s intermittently in github/CERBERUS-2.0"
- "gitea/infra/deploy-tools is retrying forever when the registry is down"

## When NOT to use it

- **The code is in the current working directory.** Use the normal Read,
  Glob, and Grep tools. FRAG works against cloned copies of *remote* repos;
  pointing it at the local checkout adds a clone and an index for no gain.
- **You already know the exact file.** Read it directly.
- **The question isn't about locating code** — architecture discussion, "how
  does this library work", writing new code from scratch.

## How to call it

Pass the user's own description of the symptom as `query`, and the repo as
`ref`:

```
frag_search(
  ref="github/CERBERUS-2.0",
  query="login returns intermittent 500s under load, need graceful degradation"
)
```

Two things matter here:

**Pass the symptom, not keywords.** FRAG does lexical retrieval and then, if
embeddings are configured, semantic re-ranking. Reducing "login returns
intermittent 500s under load" to `login` throws away the signal the second
stage runs on. Give it the sentence.

**Pass `ref` explicitly when you know it.** FRAG can extract a reference
from free text as a fallback, but that's a guess. If the user named the
repo, put it in `ref`.

## Reading the results

Each fragment comes back with `path`, `start_line`, `end_line`, and a
`score`. These are *candidates*, not answers. Normal next steps:

1. Look at the top fragments and decide which are actually implicated.
2. If a fragment looks relevant but is missing context (you can see a
   function's middle but not its signature), that's expected — chunks are
   line windows. Ask FRAG again with a more specific query, or note the
   path for the user to open.
3. If nothing relevant comes back, say so rather than guessing. A
   reformulated query with different vocabulary is worth one retry; beyond
   that, tell the user FRAG didn't surface anything and ask how they'd like
   to proceed.

## Do not present fragments as a diagnosis

FRAG tells you what's *textually and semantically near* the symptom
description. That is not the same as what's causing the problem. Present
fragments as "here's what looks related", and reason from there — don't
assert a root cause on retrieval alone.

## The other tools

- `frag_resolve(ref)` — sync a repo's clone and index without searching.
  Useful to pre-warm a large repo, or to check that credentials work.
- `frag_status(ref)` — report what's indexed, without touching the network.

The first `frag_search` against a repo clones and indexes it, so it can take
noticeably longer than subsequent ones. That's expected; don't retry
thinking it hung.
