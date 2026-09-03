"""
The single entry point everything else calls: given a reference like
"github/CERBERUS-2.0" (or free text containing one), get back a RepoHandle
whose store is guaranteed to reflect the current worktree.

    handle = resolve("github/CERBERUS-2.0")
    fragments = search(handle, "noticing 500s on login, need graceful fallback")

Scope is enforced by construction: `handle.store` is one SQLite file for
exactly one repo. There is no operation on a RepoHandle that can touch any
other repo's data.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from frag.hosts import KNOWN_HOSTS, get_provider, extract_ref_from_text, parse_ref
from frag.hosts.base import RepoRef
from frag.indexer import Indexer, SyncReport
from frag.store import Store


def _frag_home() -> Path:
    return Path(os.environ.get("FRAG_HOME", str(Path.home() / ".frag")))


def _worktree_path(ref: RepoRef) -> Path:
    return _frag_home() / "clones" / ref.host / ref.owner / ref.repo


def _index_path(ref: RepoRef) -> Path:
    return _frag_home() / "index" / ref.host / ref.owner / f"{ref.repo}.sqlite"


@dataclass
class RepoHandle:
    ref: RepoRef
    worktree: Path
    store: Store
    last_sync: SyncReport


def resolve(ref_text: str | None, *, free_text: str | None = None, force_full_resync: bool = False) -> RepoHandle:
    """
    ref_text: an explicit 'host[/owner]/repo' string, preferred.
    free_text: a query string to scan for a ref if ref_text wasn't given.
    """
    parsed = None
    if ref_text:
        parsed = parse_ref(ref_text, KNOWN_HOSTS)
        if parsed is None:
            raise ValueError(f"{ref_text!r} does not match host[/owner]/repo grammar or names an unknown host")
    elif free_text:
        parsed = extract_ref_from_text(free_text, KNOWN_HOSTS)
        if parsed is None:
            raise ValueError("no repo reference found in free text and none was given explicitly")
    else:
        raise ValueError("resolve() needs either ref_text or free_text")

    host, owner, repo = parsed
    provider = get_provider(host)
    ref = provider.resolve(owner, repo)

    worktree = _worktree_path(ref)
    sync_result = provider.sync_worktree(ref, worktree)

    store = Store(_index_path(ref))
    indexer = Indexer(worktree, store)

    if force_full_resync or sync_result.changed_paths is None:
        report = indexer.sync(changed_paths=None)
    else:
        report = indexer.sync(changed_paths=sync_result.changed_paths)

    return RepoHandle(ref=ref, worktree=worktree, store=store, last_sync=report)
