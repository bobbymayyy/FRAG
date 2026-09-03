"""Local-first source acquisition for repository hubs.

FRAG normally gets bytes from a HostProvider (GitHub/Gitea). On repository
management hosts such as /srv/repos that is wasteful: the working clone or a
local bare mirror already contains the repository. This module discovers and
materializes those local sources without mutating the managed hub.

Auto source order:

    working clone -> bare mirror -> newest archive snapshot -> remote fallback

Managed hub inputs are read-only from FRAG's point of view. Bare mirrors and
archive snapshots are materialized under FRAG_HOME before indexing. Archive
materialization never indexes repository data directly from the snapshot; it
extracts the selected bare mirror to a temporary directory, then uses
``git archive`` plus a path-safe Python tar reader to produce a clean source
tree.
"""

from __future__ import annotations

import configparser
import io
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

from frag.hosts.base import RepoRef, SyncResult

DEFAULT_HUB = Path("/srv/repos")
SOURCE_KINDS = {"auto", "worktree", "mirror", "archive", "remote"}


@dataclass(frozen=True)
class LocalSource:
    ref: RepoRef
    worktree: Path
    sync_result: SyncResult
    kind: str
    source_path: Path

    @property
    def identity(self) -> str:
        return f"{self.kind}:{self.source_path.resolve()}"


def repo_hub() -> Path | None:
    configured = os.environ.get("FRAG_REPO_HUB", "").strip()
    if configured:
        path = Path(configured).expanduser()
        return path if path.is_dir() else None
    return DEFAULT_HUB if DEFAULT_HUB.is_dir() else None


def _default_owner(host: str) -> str | None:
    value = os.environ.get(f"FRAG_{host.upper()}_DEFAULT_OWNER", "").strip()
    return value or None


def _remote_url_from_config_text(text: str) -> str | None:
    """Read remote.origin.url without asking git to process include files."""
    parser = configparser.RawConfigParser(strict=False)
    try:
        parser.read_file(io.StringIO(text))
    except (configparser.Error, UnicodeError):
        return None
    section = 'remote "origin"'
    if not parser.has_section(section):
        return None
    value = parser.get(section, "url", fallback="").strip()
    return value or None


def _remote_url_from_repo(path: Path, *, bare: bool = False) -> str | None:
    config_path = path / "config" if bare else path / ".git" / "config"
    try:
        return _remote_url_from_config_text(config_path.read_text(encoding="utf-8"))
    except OSError:
        return None


def _url_path_parts(url: str) -> tuple[str | None, list[str]]:
    """Return hostname + path parts for HTTPS, SSH, and scp-style git URLs."""
    if "://" in url:
        parsed = urlparse(url)
        parts = [p for p in parsed.path.strip("/").split("/") if p]
        return parsed.hostname, parts
    if "@" in url and ":" in url:
        host_part, path = url.split(":", 1)
        hostname = host_part.rsplit("@", 1)[-1]
        parts = [p for p in path.strip("/").split("/") if p]
        return hostname, parts
    parts = [p for p in url.strip("/").split("/") if p]
    return None, parts


def _remote_identity(url: str | None) -> tuple[str | None, str | None, str | None]:
    if not url:
        return None, None, None
    hostname, parts = _url_path_parts(url)
    if len(parts) < 2:
        return hostname, None, None
    repo = parts[-1]
    if repo.endswith(".git"):
        repo = repo[:-4]
    return hostname, parts[-2], repo


def _host_matches(host: str, hostname: str | None) -> bool:
    if hostname is None:
        return True
    hostname = hostname.lower()
    if host == "github":
        return hostname == "github.com"
    configured = os.environ.get("FRAG_GITEA_URL", "").strip()
    if configured:
        wanted = urlparse(configured).hostname
        return wanted is None or hostname == wanted.lower()
    return hostname != "github.com"


def _resolved_ref(host: str, owner: str | None, repo: str, remote_url: str | None) -> RepoRef | None:
    hostname, inferred_owner, inferred_repo = _remote_identity(remote_url)
    if inferred_repo and inferred_repo != repo:
        return None
    if not _host_matches(host, hostname):
        return None
    if owner and inferred_owner and owner != inferred_owner:
        return None
    resolved_owner = owner or inferred_owner or _default_owner(host) or "_local"
    return RepoRef(host=host, owner=resolved_owner, repo=repo)


def _git_head(path: Path, *, bare: bool = False) -> str:
    args = ["git"]
    if bare:
        args.extend(["--git-dir", str(path)])
    else:
        args.extend(["-C", str(path)])
    args.extend(["rev-parse", "HEAD"])
    proc = subprocess.run(args, capture_output=True, text=True, timeout=30, check=False)
    if proc.returncode == 0 and proc.stdout.strip():
        return proc.stdout.strip()
    return "unknown"


def _find_worktree(hub: Path, host: str, owner: str | None, repo: str) -> LocalSource | None:
    path = hub / host / repo
    if not path.is_dir() or not (path / ".git").exists():
        return None
    ref = _resolved_ref(host, owner, repo, _remote_url_from_repo(path))
    if ref is None:
        return None
    head = _git_head(path)
    return LocalSource(
        ref=ref,
        worktree=path,
        sync_result=SyncResult(False, None, head, head),
        kind="worktree",
        source_path=path,
    )


def _walk_mirror_candidates(root: Path, repo: str) -> list[Path]:
    if not root.is_dir():
        return []
    wanted = f"{repo}.git"
    found: list[Path] = []
    # Bound traversal depth and prune bare repos so we never walk object trees.
    for current, dirs, _files in os.walk(root):
        current_path = Path(current)
        try:
            depth = len(current_path.relative_to(root).parts)
        except ValueError:
            continue
        if current_path.name.endswith(".git"):
            if current_path.name == wanted:
                found.append(current_path)
            dirs[:] = []
            continue
        if depth >= 3:
            dirs[:] = []
    return found


def _source_meta_path(frag_home: Path, kind: str, ref: RepoRef) -> Path:
    return frag_home / "source-meta" / kind / ref.host / ref.owner / f"{ref.repo}.json"


def _materialized_path(frag_home: Path, kind: str, ref: RepoRef) -> Path:
    return frag_home / "materialized" / kind / ref.host / ref.owner / ref.repo


def _read_source_meta(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_source_meta(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
    os.replace(temp, path)


def _safe_tar_member(name: str) -> PurePosixPath:
    rel = PurePosixPath(name)
    if rel.is_absolute() or not rel.parts or any(part in {"", ".", ".."} for part in rel.parts):
        raise RuntimeError(f"unsafe path in git archive: {name!r}")
    return rel


def _extract_git_archive(git_dir: Path, target: Path) -> str:
    """Materialize HEAD from a bare repo without preserving symlinks."""
    head = _git_head(git_dir, bare=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp: Path | None = Path(tempfile.mkdtemp(prefix=f".{target.name}.tmp-", dir=target.parent))
    proc: subprocess.Popen[bytes] | None = None
    try:
        proc = subprocess.Popen(
            ["git", "--git-dir", str(git_dir), "archive", "--format=tar", "HEAD"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert proc.stdout is not None
        with tarfile.open(fileobj=proc.stdout, mode="r|") as archive:
            for member in archive:
                rel = _safe_tar_member(member.name)
                destination = temp.joinpath(*rel.parts)
                if member.isdir():
                    destination.mkdir(parents=True, exist_ok=True)
                    continue
                if not member.isfile():
                    # Symlinks/hardlinks/devices are intentionally omitted.
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                source = archive.extractfile(member)
                if source is None:
                    continue
                with source, destination.open("wb") as out:
                    shutil.copyfileobj(source, out)
                try:
                    destination.chmod(member.mode & 0o777)
                except OSError:
                    pass
        stderr = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
        returncode = proc.wait(timeout=60)
        if returncode != 0:
            raise RuntimeError(f"git archive failed for local mirror: {stderr.strip()}")
        if target.exists():
            shutil.rmtree(target)
        os.replace(temp, target)
        temp = None
        return head
    finally:
        if proc is not None and proc.poll() is None:
            proc.kill()
            proc.wait()
        if temp is not None:
            shutil.rmtree(temp, ignore_errors=True)


def _materialize_bare(
    git_dir: Path,
    *,
    frag_home: Path,
    ref: RepoRef,
    kind: str,
    source_key: str,
) -> tuple[Path, SyncResult]:
    target = _materialized_path(frag_home, kind, ref)
    meta_path = _source_meta_path(frag_home, kind, ref)
    head = _git_head(git_dir, bare=True)
    meta = _read_source_meta(meta_path)
    if target.is_dir() and meta.get("source_key") == source_key and meta.get("head") == head:
        return target, SyncResult(False, [], head, head)

    old_head = meta.get("head") if isinstance(meta.get("head"), str) else None
    materialized_head = _extract_git_archive(git_dir, target)
    _write_source_meta(meta_path, {"source_key": source_key, "head": materialized_head})
    return target, SyncResult(False, None, old_head, materialized_head)


def _find_mirror(
    hub: Path,
    host: str,
    owner: str | None,
    repo: str,
    frag_home: Path,
) -> LocalSource | None:
    for mirror in _walk_mirror_candidates(hub / "mirrors", repo):
        ref = _resolved_ref(host, owner, repo, _remote_url_from_repo(mirror, bare=True))
        if ref is None:
            continue
        worktree, sync = _materialize_bare(
            mirror,
            frag_home=frag_home,
            ref=ref,
            kind="mirror",
            source_key=str(mirror.resolve()),
        )
        return LocalSource(ref, worktree, sync, "mirror", mirror)
    return None


def _archive_candidates(archive: Path, repo: str) -> list[str]:
    if shutil.which("tar") is None:
        raise RuntimeError("archive source requires GNU tar with zstd support")
    proc = subprocess.run(
        ["tar", "--zstd", "-tf", str(archive)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"could not list archive {archive.name}: {proc.stderr.strip()}")

    suffix = f"/{repo}.git/HEAD"
    prefixes: list[str] = []
    for raw in proc.stdout.splitlines():
        raw = raw.strip()
        normalized = raw[2:] if raw.startswith("./") else raw
        if normalized != f"{repo}.git/HEAD" and not normalized.endswith(suffix):
            continue
        raw_prefix = raw[:-5]  # strip '/HEAD' but preserve exact archive spelling
        rel = PurePosixPath(raw_prefix)
        if rel.is_absolute() or any(part in {"", ".."} for part in rel.parts):
            continue
        prefixes.append(raw_prefix)
    return prefixes


def _archive_member_text(archive: Path, member: str) -> str | None:
    proc = subprocess.run(
        ["tar", "--zstd", "-xOf", str(archive), member],
        capture_output=True,
        timeout=60,
        check=False,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout.decode("utf-8", "replace")


def _extract_archive_mirror(archive: Path, prefix: str, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [
            "tar",
            "--zstd",
            "--no-same-owner",
            "--no-same-permissions",
            "-xf",
            str(archive),
            "-C",
            str(destination),
            prefix,
        ],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"could not extract {prefix} from {archive.name}: {proc.stderr.strip()}")
    git_dir = destination.joinpath(*PurePosixPath(prefix).parts)
    if not git_dir.is_dir():
        raise RuntimeError(f"archive member {prefix!r} did not materialize as a bare repository")
    return git_dir


def _find_archive(
    hub: Path,
    host: str,
    owner: str | None,
    repo: str,
    frag_home: Path,
) -> LocalSource | None:
    archive_root = hub / "archive"
    if not archive_root.is_dir():
        return None
    archives = sorted(archive_root.glob("*.tar.zst"), key=lambda p: p.stat().st_mtime, reverse=True)
    for archive in archives:
        for prefix in _archive_candidates(archive, repo):
            config_text = _archive_member_text(archive, f"{prefix}/config")
            remote_url = _remote_url_from_config_text(config_text) if config_text else None
            ref = _resolved_ref(host, owner, repo, remote_url)
            if ref is None:
                continue
            source_key = f"{archive.resolve()}:{archive.stat().st_mtime_ns}:{prefix}"
            target = _materialized_path(frag_home, "archive", ref)
            meta_path = _source_meta_path(frag_home, "archive", ref)
            meta = _read_source_meta(meta_path)
            if target.is_dir() and meta.get("source_key") == source_key:
                head = str(meta.get("head") or "unknown")
                return LocalSource(ref, target, SyncResult(False, [], head, head), "archive", archive)

            with tempfile.TemporaryDirectory(prefix="frag-archive-") as temp_name:
                git_dir = _extract_archive_mirror(archive, prefix, Path(temp_name))
                worktree, sync = _materialize_bare(
                    git_dir,
                    frag_home=frag_home,
                    ref=ref,
                    kind="archive",
                    source_key=source_key,
                )
            return LocalSource(ref, worktree, sync, "archive", archive)
    return None


def acquire_local(
    host: str,
    owner: str | None,
    repo: str,
    *,
    frag_home: Path,
    source: str = "auto",
) -> LocalSource | None:
    """Acquire a local source for ``host[/owner]/repo``.

    ``source`` may be auto/worktree/mirror/archive/remote. ``remote`` always
    returns None so the caller can invoke its HostProvider. An explicit local
    source raises when unavailable rather than silently going to the network.
    """
    if source not in SOURCE_KINDS:
        raise ValueError(f"unknown source {source!r}; expected one of {sorted(SOURCE_KINDS)}")
    if source == "remote":
        return None

    hub = repo_hub()
    if hub is None:
        if source == "auto":
            return None
        raise RuntimeError("local repository hub is unavailable; set FRAG_REPO_HUB or provide /srv/repos")

    if source in {"auto", "worktree"}:
        found = _find_worktree(hub, host, owner, repo)
        if found is not None:
            return found
        if source == "worktree":
            raise FileNotFoundError(f"no local working clone for {host}/{repo} under {hub}")

    if source in {"auto", "mirror"}:
        found = _find_mirror(hub, host, owner, repo, frag_home)
        if found is not None:
            return found
        if source == "mirror":
            raise FileNotFoundError(f"no local bare mirror for {host}/{repo} under {hub / 'mirrors'}")

    if source in {"auto", "archive"}:
        try:
            found = _find_archive(hub, host, owner, repo, frag_home)
        except RuntimeError:
            if source == "archive":
                raise
            found = None
        if found is not None:
            return found
        if source == "archive":
            raise FileNotFoundError(f"no archive snapshot containing {host}/{repo} under {hub / 'archive'}")

    return None
