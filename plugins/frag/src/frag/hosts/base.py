"""
Host provider abstraction.

A HostProvider knows how to turn (owner, repo) into a concrete clone URL and
how to get a local worktree in sync with the remote. It does NOT know
anything about indexing, chunking, or storage -- that's the Indexer/Store's
job. This keeps "how do I get the bytes" cleanly separate from "what do I do
with the bytes."

Reference grammar (what a caller types or a query contains):

    host[/owner]/repo

Examples:
    github/CERBERUS-2.0            -> host=github, owner=<default>, repo=CERBERUS-2.0
    github/some-org/CERBERUS-2.0   -> host=github, owner=some-org,  repo=CERBERUS-2.0
    gitea/infra-team/deploy-tools  -> host=gitea,  owner=infra-team, repo=deploy-tools

If owner is omitted, the provider falls back to its own default-owner env
var. If there is no default either, resolution fails loudly -- FRAG does not
guess an owner.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class RepoRef:
    host: str      # provider alias, e.g. "github", "gitea"
    owner: str
    repo: str

    @property
    def key(self) -> str:
        """Filesystem/index-safe identifier: host/owner/repo."""
        return f"{self.host}/{self.owner}/{self.repo}"


@dataclass
class SyncResult:
    cloned: bool                 # True if this was a fresh clone, False if it was a pull
    changed_paths: list[str] | None  # None means "treat as full resync", e.g. after a fresh clone
    head_before: str | None
    head_after: str


class HostProvider(Protocol):
    alias: str

    def resolve(self, owner: str | None, repo: str) -> RepoRef:
        """Fill in a default owner if needed; raise if it can't be resolved."""
        ...

    def clone_url(self, ref: RepoRef) -> str:
        ...

    def sync_worktree(self, ref: RepoRef, path: Path) -> SyncResult:
        """Clone into `path` if absent, else fetch+pull. Returns what changed."""
        ...


# host[/owner]/repo  -- owner segment is optional
_REF_RE = re.compile(
    r"^(?P<host>[a-zA-Z0-9_-]+)/(?:(?P<owner>[a-zA-Z0-9_.-]+)/)?(?P<repo>[a-zA-Z0-9_.-]+)$"
)

# Looser pattern for pulling a ref out of free-text queries, e.g.
# "check github/CERBERUS-2.0 for the auth bug" -> "github/CERBERUS-2.0"
_FREE_TEXT_RE = re.compile(
    r"(?<![\w/])(?P<full>[a-zA-Z0-9_-]+/(?:[a-zA-Z0-9_.-]+/)?[a-zA-Z0-9_.-]+)(?![\w/])"
)


def parse_ref(text: str, known_hosts: set[str]) -> tuple[str, str | None, str] | None:
    """
    Parse an explicit 'host[/owner]/repo' string. Returns (host, owner, repo)
    or None if it doesn't match the grammar or the host isn't known.
    """
    m = _REF_RE.match(text.strip())
    if not m:
        return None
    host = m.group("host")
    if host not in known_hosts:
        return None
    return host, m.group("owner"), m.group("repo")


def extract_ref_from_text(text: str, known_hosts: set[str]) -> tuple[str, str | None, str] | None:
    """
    Best-effort scan of free text for something matching the ref grammar
    where the leading segment is a known host alias. Used only as a fallback
    when no explicit ref argument was supplied.
    """
    for m in _FREE_TEXT_RE.finditer(text):
        candidate = m.group("full")
        parsed = parse_ref(candidate, known_hosts)
        if parsed:
            return parsed
    return None
