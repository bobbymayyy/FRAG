from __future__ import annotations

import os
from pathlib import Path

from frag.hosts.base import RepoRef, SyncResult
from frag.hosts.gitutil import clone_or_pull


class GiteaProvider:
    """
    HostProvider for a self-hosted Gitea instance.

    Unlike GitHub, there's no fixed domain -- FRAG_GITEA_URL is required,
    not optional, and has no sensible default. Eager validation at
    construction means a missing/misconfigured Gitea env skips this plugin
    cleanly instead of failing deep inside a clone attempt.
    """

    alias = "gitea"

    def __init__(self) -> None:
        base_url = os.environ.get("FRAG_GITEA_URL")
        token = os.environ.get("FRAG_GITEA_TOKEN")
        if not base_url:
            raise RuntimeError("FRAG_GITEA_URL is not set")
        if not token:
            raise RuntimeError("FRAG_GITEA_TOKEN is not set")
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._default_owner = os.environ.get("FRAG_GITEA_DEFAULT_OWNER")

    def resolve(self, owner: str | None, repo: str) -> RepoRef:
        owner = owner or self._default_owner
        if not owner:
            raise ValueError(
                f"no owner given for gitea/{repo} and FRAG_GITEA_DEFAULT_OWNER is not set"
            )
        return RepoRef(host=self.alias, owner=owner, repo=repo)

    def clone_url(self, ref: RepoRef) -> str:
        # base_url is expected to be e.g. https://git.internal.example
        scheme, _, rest = self._base_url.partition("://")
        return f"{scheme}://{self._token}@{rest}/{ref.owner}/{ref.repo}.git"

    def sync_worktree(self, ref: RepoRef, path: Path) -> SyncResult:
        return clone_or_pull(self.clone_url(ref), path)
