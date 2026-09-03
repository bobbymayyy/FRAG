from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from frag.indexer import Indexer
from frag.mcp_server import frag_status
from frag.resolve import resolve
from frag.retriever import search
from frag.store import Store


def _run(*args: str, cwd: Path | None = None) -> None:
    proc = subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr


def _write_origin_config(git_dir: Path, url: str) -> None:
    config = git_dir / "config"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(f'[remote "origin"]\n\turl = {url}\n', encoding="utf-8")


def _configure_hub(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Path, Path]:
    hub = tmp_path / "hub"
    frag_home = tmp_path / "frag-home"
    hub.mkdir()
    monkeypatch.setenv("FRAG_REPO_HUB", str(hub))
    monkeypatch.setenv("FRAG_HOME", str(frag_home))
    monkeypatch.delenv("FRAG_GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("FRAG_GITHUB_DEFAULT_OWNER", raising=False)
    return hub, frag_home


def test_local_worktree_resolves_without_remote_credentials(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    hub, _frag_home = _configure_hub(monkeypatch, tmp_path)
    repo = hub / "github" / "demo"
    repo.mkdir(parents=True)
    _write_origin_config(repo / ".git", "https://github.com/bobbymayyy/demo.git")
    (repo / "app.py").write_text("def local_only():\n    return 'needle'\n", encoding="utf-8")

    handle = resolve("github/demo")
    try:
        assert handle.ref.owner == "bobbymayyy"
        assert handle.source_kind == "worktree"
        assert handle.worktree == repo
        hits = search(handle.store, "needle")
        assert hits and hits[0].path == "app.py"
    finally:
        handle.store.close()


def test_remote_mode_does_not_silently_use_local_worktree(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    hub, frag_home = _configure_hub(monkeypatch, tmp_path)
    repo = hub / "github" / "demo"
    repo.mkdir(parents=True)
    _write_origin_config(repo / ".git", "https://github.com/bobbymayyy/demo.git")
    (repo / "app.py").write_text("needle\n", encoding="utf-8")

    # No credentials means the remote provider cannot construct. If this call
    # accidentally used the local worktree it would succeed instead.
    with pytest.raises(RuntimeError):
        resolve("github/bobbymayyy/demo", source="remote")
    assert not (frag_home / "index" / "github" / "bobbymayyy" / "demo.sqlite").exists()


def test_status_works_from_local_identity_without_credentials(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    hub, _frag_home = _configure_hub(monkeypatch, tmp_path)
    repo = hub / "github" / "demo"
    repo.mkdir(parents=True)
    _write_origin_config(repo / ".git", "https://github.com/bobbymayyy/demo.git")
    (repo / "app.py").write_text("def status_needle():\n    pass\n", encoding="utf-8")

    handle = resolve("github/demo")
    handle.store.close()
    status = frag_status("github/demo")
    assert status["indexed"] is True
    assert status["source"] == "worktree"


def test_symlink_cannot_escape_repository_tree(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("ULTRA_PRIVATE_NEEDLE\n", encoding="utf-8")
    (repo / "safe.py").write_text("SAFE_NEEDLE = True\n", encoding="utf-8")
    try:
        (repo / "leak.py").symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    store = Store(tmp_path / "index.sqlite")
    try:
        Indexer(repo, store).sync(changed_paths=None)
        assert search(store, "ULTRA_PRIVATE_NEEDLE") == []
        assert search(store, "SAFE_NEEDLE")
        assert "leak.py" not in store.all_known_paths()
    finally:
        store.close()


def _make_bare_mirror(hub: Path, tmp_path: Path) -> Path:
    seed = tmp_path / "seed"
    seed.mkdir()
    _run("git", "init", "-q", cwd=seed)
    _run("git", "config", "user.email", "ci@example.invalid", cwd=seed)
    _run("git", "config", "user.name", "CI", cwd=seed)
    (seed / "mirror.py").write_text("MIRROR_NEEDLE = 1\n", encoding="utf-8")
    _run("git", "add", "mirror.py", cwd=seed)
    _run("git", "commit", "-qm", "seed", cwd=seed)

    mirror = hub / "mirrors" / "demo.git"
    mirror.parent.mkdir(parents=True)
    _run("git", "clone", "--mirror", str(seed), str(mirror))
    _run(
        "git",
        "--git-dir",
        str(mirror),
        "config",
        "remote.origin.url",
        "https://github.com/bobbymayyy/demo.git",
    )
    return mirror


def test_bare_mirror_is_materialized_without_modifying_mirror(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    hub, _frag_home = _configure_hub(monkeypatch, tmp_path)
    mirror = _make_bare_mirror(hub, tmp_path)
    before_head = subprocess.check_output(
        ["git", "--git-dir", str(mirror), "rev-parse", "HEAD"], text=True
    ).strip()

    handle = resolve("github/bobbymayyy/demo", source="mirror")
    try:
        assert handle.source_kind == "mirror"
        assert handle.worktree != mirror
        assert not (handle.worktree / ".git").exists()
        assert search(handle.store, "MIRROR_NEEDLE")
    finally:
        handle.store.close()

    after_head = subprocess.check_output(
        ["git", "--git-dir", str(mirror), "rev-parse", "HEAD"], text=True
    ).strip()
    assert after_head == before_head


def test_tar_zst_archive_can_supply_repository(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    if shutil.which("tar") is None or shutil.which("zstd") is None:
        pytest.skip("tar+zstd not available")

    hub, _frag_home = _configure_hub(monkeypatch, tmp_path)
    mirror = _make_bare_mirror(hub, tmp_path)
    archive_dir = hub / "archive"
    archive_dir.mkdir()
    archive = archive_dir / "snapshot.tar.zst"

    proc = subprocess.run(
        ["tar", "--zstd", "-cf", str(archive), "-C", str(hub), "mirrors"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        pytest.skip(f"tar lacks working zstd support: {proc.stderr}")

    # Explicit archive mode must not quietly choose the fresher bare mirror.
    shutil.rmtree(mirror)
    handle = resolve("github/bobbymayyy/demo", source="archive")
    try:
        assert handle.source_kind == "archive"
        assert handle.source_path == archive
        assert search(handle.store, "MIRROR_NEEDLE")
    finally:
        handle.store.close()
