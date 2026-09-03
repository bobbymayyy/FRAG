"""
Indexer: walks a worktree (or a delta list of changed paths) and brings the
Store up to date with it.

Security invariant: FRAG indexes regular files physically contained within
the selected repository tree. Symlinks are never followed. This matters for
local-hub operation where a tracked or untracked symlink could otherwise make
a repository path resolve into unrelated host data.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from frag import firewall
from frag.chunker import LineChunker
from frag.embedder import try_get_embedder
from frag.store import Store


@dataclass
class SyncReport:
    accepted: int = 0
    rejected: int = 0
    evicted: int = 0
    embedding_degraded: bool = False
    degrade_reason: str = ""
    errors: list[str] = field(default_factory=list)


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class Indexer:
    def __init__(self, repo_root: Path, store: Store) -> None:
        self.repo_root = repo_root
        self._resolved_root = repo_root.resolve()
        self.store = store
        self.chunker = LineChunker()
        self.embedder = try_get_embedder()
        self._embeddings_enabled = self._resolve_fingerprint()

    def _resolve_fingerprint(self) -> bool:
        stored = self.store.get_fingerprint()
        if self.embedder is None:
            return False
        current = (self.embedder.name, self.embedder.dim)
        if stored is None:
            self.store.set_fingerprint(*current)
            return True
        if stored != current:
            return False
        return True

    def _is_contained_regular_file(self, path: Path) -> bool:
        if path.is_symlink() or not path.is_file():
            return False
        try:
            path.resolve().relative_to(self._resolved_root)
        except (OSError, ValueError):
            return False
        return True

    def _iter_repo_files(self) -> list[Path]:
        return [p for p in self.repo_root.rglob("*") if self._is_contained_regular_file(p)]

    def sync(self, changed_paths: list[str] | None = None) -> SyncReport:
        report = SyncReport()
        if self.embedder is not None and not self._embeddings_enabled:
            report.embedding_degraded = True
            report.degrade_reason = (
                f"embedder fingerprint mismatch (store has {self.store.get_fingerprint()}, "
                f"active embedder is ({self.embedder.name}, {self.embedder.dim}))"
            )

        if changed_paths is None:
            self._full_sync(report)
        else:
            self._delta_sync(changed_paths, report)

        self.store.commit()
        return report

    def _reject_path(self, abs_path: Path, rel_path: str, reason: str, report: SyncReport) -> None:
        was_known = self.store.get_file_hash(rel_path) is not None
        if was_known:
            report.evicted += self.store.evict_path(rel_path) or 1
        try:
            mtime = abs_path.lstat().st_mtime
        except OSError:
            mtime = 0.0
        self.store.upsert_file(rel_path, "", mtime, "rejected", reason)
        report.rejected += 1

    def _process_file(self, abs_path: Path, rel_path: str, report: SyncReport) -> None:
        if abs_path.is_symlink():
            self._reject_path(abs_path, rel_path, "symlink not indexed", report)
            return

        if not abs_path.exists():
            if self.store.get_file_hash(rel_path) is not None:
                report.evicted += self.store.evict_path(rel_path) or 1
            return

        if not self._is_contained_regular_file(abs_path):
            self._reject_path(abs_path, rel_path, "path is not a contained regular file", report)
            return

        verdict = firewall.check(abs_path, repo_root=self.repo_root)
        if not verdict.accepted:
            self._reject_path(abs_path, rel_path, verdict.reason, report)
            return

        try:
            text = abs_path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError) as exc:
            report.errors.append(f"{rel_path}: {exc}")
            return

        content_hash = _hash_text(text)
        if self.store.get_file_hash(rel_path) == content_hash:
            report.accepted += 1
            return

        chunks = self.chunker.chunk(text)
        embeddings = None
        if self._embeddings_enabled and self.embedder is not None and chunks:
            embeddings = self.embedder.embed([c.text for c in chunks])

        self.store.replace_chunks(
            rel_path,
            [(c.start_line, c.end_line, c.text, _hash_text(c.text)) for c in chunks],
            embeddings,
        )
        self.store.upsert_file(rel_path, content_hash, abs_path.stat().st_mtime, "accepted", "accepted")
        report.accepted += 1

    def _full_sync(self, report: SyncReport) -> None:
        seen: set[str] = set()
        for abs_path in self._iter_repo_files():
            rel = abs_path.relative_to(self.repo_root)
            if any(seg in firewall.DENY_PATH_SEGMENTS for seg in rel.parts):
                continue
            rel_path = str(rel)
            seen.add(rel_path)
            self._process_file(abs_path, rel_path, report)

        # A symlink is intentionally absent from the walk. Evict any older
        # accepted row for it rather than leaving stale searchable content.
        for rel_path in self.store.all_known_paths() - seen:
            report.evicted += self.store.evict_path(rel_path) or 1

    def _delta_sync(self, changed_paths: list[str], report: SyncReport) -> None:
        for rel_path in changed_paths:
            abs_path = self.repo_root / rel_path
            self._process_file(abs_path, rel_path, report)
