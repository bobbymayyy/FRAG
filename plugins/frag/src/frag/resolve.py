"""Resolve repository references into an indexed source tree.

The default source policy is local-first. On repository hubs such as
``/srv/repos``, FRAG uses an existing working clone, then a local bare mirror,
then the newest archive snapshot before falling back to GitHub/Gitea network
sync. Managed hub inputs are never modified.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from frag.hosts import KNOWN_HOSTS, extract_ref_from_text, get_provider, parse_ref
from frag.hosts.base import RepoRef
from frag.indexer import Indexer, SyncReport
from frag.local_sources import SOURCE_KINDS, acquire_local
from frag.store import Store


def _frag_home() -> Path:
    return Path(os.environ.get("FRAG_HOME", str(Path.home() / ".frag")))


def _worktree_path(ref: RepoRef) -> Path:
    return _frag_home() / "clones" / ref.host / ref.owner / ref.repo


def _index_path(ref: RepoRef) -> Path:
    return _frag_home() / "index" / ref.host / ref.owner / f"{ref.repo}.sqlite"


def _source_marker_path(ref: RepoRef) -> Path:
    return _index_path(ref).with_suffix(".source")


def _read_source_marker(ref: RepoRef) -> str | None:
    try:
        value = _source_marker_path(ref).read_text(encoding="utf-8").strip()
        return value or None
    except OSError:
        return None


def _write_source_marker(ref: RepoRef, identity: str) -> None:
    path = _source_marker_path(ref)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(identity + "\n", encoding="utf-8")
    os.replace(temp, path)


@dataclass
class RepoHandle:
    ref: RepoRef
    worktree: Path
    store: Store
    last_sync: SyncReport
    source_kind: str
    source_path: Path


def _parse_reference(ref_text: str | None, free_text: str | None) -> tuple[str, str | None, str]:
    if ref_text:
        parsed = parse_ref(ref_text, KNOWN_HOSTS)
        if parsed is None:
            raise ValueError(
                f"{ref_text!r} does not match host[/owner]/repo grammar or names an unknown host"
            )
        return parsed
    if free_text:
        parsed = extract_ref_from_text(free_text, KNOWN_HOSTS)
        if parsed is None:
            raise ValueError("no repo reference found in free text and none was given explicitly")
        return parsed
    raise ValueError("resolve() needs either ref_text or free_text")


def resolve(
    ref_text: str | None,
    *,
    free_text: str | None = None,
    force_full_resync: bool = False,
    source: str = "auto",
) -> RepoHandle:
    """Resolve and index a repository.

    ``source`` controls where bytes come from:

    - ``auto`` (default): local worktree -> mirror -> archive -> remote
    - ``worktree``: require the hub's live working clone
    - ``mirror``: require a local bare mirror and materialize its HEAD
    - ``archive``: require the newest matching ``tar.zst`` snapshot
    - ``remote``: bypass the hub and use the GitHub/Gitea provider

    Local working clones are full-walk indexed each call so dirty and
    untracked edits are visible immediately. Content hashes still prevent
    unchanged files from being re-chunked.
    """
    if source not in SOURCE_KINDS:
        raise ValueError(f"unknown source {source!r}; expected one of {sorted(SOURCE_KINDS)}")

    host, owner, repo = _parse_reference(ref_text, free_text)
    local = acquire_local(host, owner, repo, frag_home=_frag_home(), source=source)

    if local is not None:
        ref = local.ref
        worktree = local.worktree
        sync_result = local.sync_result
        source_kind = local.kind
        source_path = local.source_path
        source_identity = local.identity
    else:
        provider = get_provider(host)
        ref = provider.resolve(owner, repo)
        worktree = _worktree_path(ref)
        sync_result = provider.sync_worktree(ref, worktree)
        source_kind = "remote"
        source_path = worktree
        source_identity = f"remote:{worktree.resolve()}"

    store = Store(_index_path(ref))
    indexer = Indexer(worktree, store)

    # A single repo index may be fed from a live clone, materialized mirror,
    # archive snapshot, or remote clone over its lifetime. Delta paths from
    # one tree are meaningless against another, so a source transition always
    # triggers a full reconciliation.
    source_changed = _read_source_marker(ref) != source_identity
    if force_full_resync or source_changed or sync_result.changed_paths is None:
        report = indexer.sync(changed_paths=None)
    else:
        report = indexer.sync(changed_paths=sync_result.changed_paths)

    _write_source_marker(ref, source_identity)
    return RepoHandle(
        ref=ref,
        worktree=worktree,
        store=store,
        last_sync=report,
        source_kind=source_kind,
        source_path=source_path,
    )


def resolve_index_ref(ref_text: str) -> RepoRef:
    """Resolve an index identity without requiring network credentials.

    Status calls should remain usable before GitHub/Gitea userConfig exists.
    Prefer local hub metadata, then an explicit/default owner, then an
    unambiguous existing index file.
    """
    parsed = parse_ref(ref_text, KNOWN_HOSTS)
    if parsed is None:
        raise ValueError(
            f"{ref_text!r} does not match host[/owner]/repo grammar or names an unknown host"
        )
    host, owner, repo = parsed

    local = acquire_local(host, owner, repo, frag_home=_frag_home(), source="worktree")
    if local is not None:
        return local.ref

    resolved_owner = owner or os.environ.get(f"FRAG_{host.upper()}_DEFAULT_OWNER", "").strip()
    if resolved_owner:
        return RepoRef(host=host, owner=resolved_owner, repo=repo)

    candidates = list((_frag_home() / "index" / host).glob(f"*/{repo}.sqlite"))
    if len(candidates) == 1:
        return RepoRef(host=host, owner=candidates[0].parent.name, repo=repo)
    if len(candidates) > 1:
        raise ValueError(f"multiple indexed owners found for {host}/{repo}; provide host/owner/repo")
    raise ValueError(
        f"no owner available for {host}/{repo}; provide host/owner/repo or configure a default owner"
    )
