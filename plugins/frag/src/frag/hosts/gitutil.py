from __future__ import annotations

import subprocess
from pathlib import Path

from frag.hosts.base import SyncResult


def _run(args: list[str], cwd: Path | None = None) -> str:
    result = subprocess.run(
        args, cwd=cwd, capture_output=True, text=True, timeout=300, check=False
    )
    if result.returncode != 0:
        raise RuntimeError(f"git command failed: {' '.join(args)}\n{result.stderr.strip()}")
    return result.stdout.strip()


def clone_or_pull(url: str, path: Path) -> SyncResult:
    """
    If `path` doesn't exist (or isn't a git repo yet), clone fresh -- caller
    should treat this as changed_paths=None (full resync). Otherwise fetch +
    fast-forward pull and report exactly which files changed between the old
    and new HEAD, so the caller can do a delta index sync instead of a full
    walk.

    NOTE: the auth token is embedded in `url` for this call only. It is
    never persisted to the repo's on-disk git config (we set the remote URL
    without storing credentials by using it only as the fetch/clone argument,
    not `git remote set-url`), so a stale token doesn't linger in .git/config
    across runs.
    """
    if not (path / ".git").exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        _run(["git", "clone", "--depth", "1", url, str(path)])
        head_after = _run(["git", "rev-parse", "HEAD"], cwd=path)
        return SyncResult(cloned=True, changed_paths=None, head_before=None, head_after=head_after)

    head_before = _run(["git", "rev-parse", "HEAD"], cwd=path)
    # Fetch into a throwaway ref rather than mutating the current branch blindly.
    _run(["git", "fetch", url, "HEAD"], cwd=path)
    _run(["git", "merge", "--ff-only", "FETCH_HEAD"], cwd=path)
    head_after = _run(["git", "rev-parse", "HEAD"], cwd=path)

    if head_before == head_after:
        return SyncResult(cloned=False, changed_paths=[], head_before=head_before, head_after=head_after)

    diff_out = _run(["git", "diff", "--name-only", head_before, head_after], cwd=path)
    changed_paths = [p for p in diff_out.splitlines() if p]
    return SyncResult(cloned=False, changed_paths=changed_paths, head_before=head_before, head_after=head_after)
