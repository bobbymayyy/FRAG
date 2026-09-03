from __future__ import annotations

from pathlib import Path

import pytest

from frag.hosts.base import extract_ref_from_text, parse_ref
from frag.hosts.gitutil import _redact
from frag.indexer import Indexer
from frag.retriever import search
from frag.store import Store

KNOWN = {"github", "gitea"}


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    (tmp_path / "auth.py").write_text(
        "def login(user):\n    # TODO handle rate limiting\n    return check(user)\n"
    )
    (tmp_path / "util.py").write_text("def add(a, b):\n    return a + b\n")
    (tmp_path / "blob.bin").write_bytes(bytes(range(256)) * 20)
    return tmp_path


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "index.sqlite")


def test_firewall_rejects_binary_accepts_source(repo: Path, store: Store) -> None:
    report = Indexer(repo, store).sync(changed_paths=None)
    assert report.accepted == 2
    assert report.rejected >= 1
    paths = {c.path for c in store.search_fts("def", limit=50)}
    assert paths == {"auth.py", "util.py"}


def test_deleted_file_is_evicted_from_search(repo: Path, store: Store) -> None:
    idx = Indexer(repo, store)
    idx.sync(changed_paths=None)
    assert search(store, "rate limiting")

    (repo / "auth.py").unlink()
    report = idx.sync(changed_paths=None)

    assert report.evicted >= 1
    assert search(store, "rate limiting") == []
    assert "auth.py" not in store.all_known_paths()


def test_empty_path_scope_matches_nothing_not_everything(repo: Path, store: Store) -> None:
    """Regression guard: paths=[] is a real empty scope. It must never be
    treated as 'no filter supplied'."""
    Indexer(repo, store).sync(changed_paths=None)
    assert search(store, "def", paths=[]) == []
    assert search(store, "def", paths=None) != []


def test_path_scope_restricts_to_named_files(repo: Path, store: Store) -> None:
    Indexer(repo, store).sync(changed_paths=None)
    results = search(store, "def", paths=["util.py"])
    assert results
    assert {f.path for f in results} == {"util.py"}


def test_unchanged_file_is_not_rechunked(repo: Path, store: Store) -> None:
    idx = Indexer(repo, store)
    idx.sync(changed_paths=None)
    before = [(c.id, c.path) for c in store.search_fts("def", limit=50)]
    idx.sync(changed_paths=None)
    after = [(c.id, c.path) for c in store.search_fts("def", limit=50)]
    # Same rowids => chunks were left in place rather than deleted+reinserted.
    assert before == after


def test_delta_sync_only_touches_changed_paths(repo: Path, store: Store) -> None:
    idx = Indexer(repo, store)
    idx.sync(changed_paths=None)
    (repo / "util.py").write_text("def add(a, b):\n    return a + b + 0\n")
    report = idx.sync(changed_paths=["util.py"])
    assert report.accepted == 1


def test_git_error_redaction_removes_url_credentials() -> None:
    secret = "ghp_super_secret_value"
    message = (
        f"git clone https://{secret}@github.com/org/repo.git failed; "
        f"fatal: unable to access 'https://{secret}@github.com/org/repo.git/'"
    )
    redacted = _redact(message)
    assert secret not in redacted
    assert "https://***@github.com/org/repo.git" in redacted


@pytest.mark.parametrize(
    "text,expected",
    [
        ("github/CERBERUS-2.0", ("github", None, "CERBERUS-2.0")),
        ("github/some-org/CERBERUS-2.0", ("github", "some-org", "CERBERUS-2.0")),
        ("gitea/infra/deploy-tools", ("gitea", "infra", "deploy-tools")),
        ("bitbucket/thing", None),
        ("not-a-ref", None),
    ],
)
def test_parse_ref(text: str, expected) -> None:
    assert parse_ref(text, KNOWN) == expected


def test_extract_ref_from_free_text() -> None:
    got = extract_ref_from_text(
        "Noticing 500s on login, check github/CERBERUS-2.0 for the auth path", KNOWN
    )
    assert got == ("github", None, "CERBERUS-2.0")


def test_extract_ref_returns_none_when_absent() -> None:
    assert extract_ref_from_text("no reference in this sentence", KNOWN) is None
