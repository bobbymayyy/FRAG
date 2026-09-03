"""
Indexer: walks a worktree (or a delta list of changed paths) and brings the
Store up to date with it. Every defect class from the previous FRAG
iteration is addressed structurally here rather than patched on top:

  - stale eviction: any path that becomes rejected (or disappears) is fully
    purged from files/chunks/chunks_fts/vectors, not left queryable
  - fingerprint mismatch: if the active embedder's (name, dim) doesn't match
    what's recorded in the store's meta table, this sync degrades to
    lexical-only (no vector writes) rather than silently mixing vector
    spaces
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
        self.store = store
        self.chunker = LineChunker()
        self.embedder = try_get_embedder()
        self._embeddings_enabled = self._resolve_fingerprint()

    def _resolve_fingerprint(self) -> bool:
        """Returns True if it's safe to write embeddings this sync."""
        stored = self.store.get_fingerprint()
        if self.embedder is None:
            # No embedder available at all -- lexical-only, nothing to compare.
            return False
        current = (self.embedder.name, self.embedder.dim)
        if stored is None:
            self.store.set_fingerprint(*current)
            return True
        if stored != current:
            return False
        return True

    def _iter_repo_files(self) -> list[Path]:
        return [p for p in self.repo_root.rglob("*") if p.is_file()]

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

    def _process_file(self, abs_path: Path, rel_path: str, report: SyncReport) -> None:
        if not abs_path.exists():
            if self.store.get_file_hash(rel_path) is not None:
                report.evicted += self.store.evict_path(rel_path) or 1
            return

        verdict = firewall.check(abs_path, repo_root=self.repo_root)
        if not verdict.accepted:
            was_known = self.store.get_file_hash(rel_path) is not None
            if was_known:
                report.evicted += self.store.evict_path(rel_path) or 1
            self.store.upsert_file(rel_path, "", abs_path.stat().st_mtime, "rejected", verdict.reason)
            report.rejected += 1
            return

        try:
            text = abs_path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError) as exc:
            report.errors.append(f"{rel_path}: {exc}")
            return

        content_hash = _hash_text(text)
        if self.store.get_file_hash(rel_path) == content_hash:
            report.accepted += 1
            return  # unchanged, skip re-chunking

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
            rel_path = str(abs_path.relative_to(self.repo_root))
            if any(seg in firewall.DENY_PATH_SEGMENTS for seg in abs_path.relative_to(self.repo_root).parts):
                continue
            seen.add(rel_path)
            self._process_file(abs_path, rel_path, report)

        # Anything the store knows about that we didn't see on disk this
        # walk has been deleted from the worktree -- evict it.
        for rel_path in self.store.all_known_paths() - seen:
            report.evicted += self.store.evict_path(rel_path) or 1

    def _delta_sync(self, changed_paths: list[str], report: SyncReport) -> None:
        for rel_path in changed_paths:
            abs_path = self.repo_root / rel_path
            self._process_file(abs_path, rel_path, report)
