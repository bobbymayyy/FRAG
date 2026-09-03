from __future__ import annotations

import os
import subprocess
from pathlib import Path

from frag.hosts.base import RepoRef, SyncResult
from frag.hosts.gitutil import clone_or_pull


class GitHubProvider:
    """
    HostProvider for github.com.

    Eager validation: if FRAG_GITHUB_TOKEN isn't set, __init__ raises, and
    the registry skips this plugin entirely rather than constructing a
    provider that will fail confusingly on first use.
    """

    alias = "github"

    def __init__(self) -> None:
        token = os.environ.get("FRAG_GITHUB_TOKEN")
        if not token:
            raise RuntimeError("FRAG_GITHUB_TOKEN is not set")
        self._token = token
        self._default_owner = os.environ.get("FRAG_GITHUB_DEFAULT_OWNER")

    def resolve(self, owner: str | None, repo: str) -> RepoRef:
        owner = owner or self._default_owner
        if not owner:
            raise ValueError(
                f"no owner given for github/{repo} and FRAG_GITHUB_DEFAULT_OWNER is not set"
            )
        return RepoRef(host=self.alias, owner=owner, repo=repo)

    def clone_url(self, ref: RepoRef) -> str:
        # Token-in-URL auth, scoped to this single subprocess call site only.
        return f"https://{self._token}@github.com/{ref.owner}/{ref.repo}.git"

    def sync_worktree(self, ref: RepoRef, path: Path) -> SyncResult:
        return clone_or_pull(self.clone_url(ref), path)
