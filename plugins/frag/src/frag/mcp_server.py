"""
FastMCP stdio server. Tool surface is deliberately small:

  frag_search(ref=None, query=..., top_k=8)
      Resolves the ref (or pulls one out of `query` if ref is omitted),
      syncs worktree+index, runs two-stage retrieval, returns fragments.
      This is the main tool -- "noticing X, need Y" style queries go here.

  frag_resolve(ref)
      Just does the resolve/sync step and reports what changed, without
      running a search. Useful for pre-warming or checking repo status.

  frag_status(ref)
      Read-only: reports what's currently indexed without touching the
      network or re-syncing.

No config file, no settings tool -- the only inputs are the ref grammar and
env vars (FRAG_HOME, FRAG_GITHUB_TOKEN, FRAG_GITEA_URL, etc.), consistent
with the rest of the project.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from frag.resolve import resolve
from frag.retriever import search as run_search
from frag.store import Store
from frag.resolve import _index_path  # internal but same package
from frag.hosts import KNOWN_HOSTS, get_provider, parse_ref

mcp = FastMCP("frag")


@mcp.tool()
def frag_search(query: str, ref: str | None = None, top_k: int = 8) -> dict:
    """Fragment a repo down to the pieces relevant to `query`. Provide `ref`
    (e.g. 'github/CERBERUS-2.0') explicitly when known; otherwise FRAG will
    try to find one in `query` itself."""
    handle = resolve(ref, free_text=query if ref is None else None)
    fragments = run_search(handle.store, query, top_k=top_k)
    return {
        "repo": handle.ref.key,
        "sync": {
            "accepted": handle.last_sync.accepted,
            "rejected": handle.last_sync.rejected,
            "evicted": handle.last_sync.evicted,
            "embedding_degraded": handle.last_sync.embedding_degraded,
            "degrade_reason": handle.last_sync.degrade_reason,
        },
        "fragments": [
            {"path": f.path, "start_line": f.start_line, "end_line": f.end_line, "text": f.text, "score": f.score}
            for f in fragments
        ],
    }


@mcp.tool()
def frag_resolve(ref: str, force_full_resync: bool = False) -> dict:
    """Sync a repo's worktree and index without searching. Returns what
    changed on this sync."""
    handle = resolve(ref, force_full_resync=force_full_resync)
    return {
        "repo": handle.ref.key,
        "worktree": str(handle.worktree),
        "accepted": handle.last_sync.accepted,
        "rejected": handle.last_sync.rejected,
        "evicted": handle.last_sync.evicted,
        "embedding_degraded": handle.last_sync.embedding_degraded,
        "degrade_reason": handle.last_sync.degrade_reason,
    }


@mcp.tool()
def frag_status(ref: str) -> dict:
    """Report what's currently indexed for a repo without touching the
    network. Fails clearly if nothing has been indexed yet."""
    parsed = parse_ref(ref, KNOWN_HOSTS)
    if parsed is None:
        raise ValueError(f"{ref!r} does not match host[/owner]/repo grammar or names an unknown host")
    host, owner, repo = parsed
    provider = get_provider(host)
    repo_ref = provider.resolve(owner, repo)
    index_path = _index_path(repo_ref)
    if not index_path.exists():
        return {"repo": repo_ref.key, "indexed": False}

    store = Store(index_path)
    try:
        known = store.all_known_paths()
        fingerprint = store.get_fingerprint()
        return {
            "repo": repo_ref.key,
            "indexed": True,
            "known_files": len(known),
            "embedding_fingerprint": fingerprint,
        }
    finally:
        store.close()


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
