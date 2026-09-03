"""
Per-repo SQLite store.

One store == one repo's SQLite file (~/.frag/index/<host>/<owner>/<repo>.sqlite).
Because scope is enforced by "which file did you open," not by a WHERE
clause, cross-repo leakage is structurally impossible here -- there's no
shared table for a scoped query to accidentally escape.

Tables:
  files    -- one row per source file: last-seen hash/mtime/verdict
  chunks   -- one row per chunk: path, line range, text, content hash
  chunks_fts -- FTS5 index over chunks.text, manually kept in sync (no
               triggers, so insert/delete of chunk rows and fts rows are
               explicit and easy to reason about together)
  vectors  -- optional: chunk_id -> embedding blob, only populated when an
              embedder plugin is active
  meta     -- key/value, used for the embedding fingerprint (model name +
              dimension) so switching embedding backends is detected instead
              of silently mixing incompatible vector spaces
"""

from __future__ import annotations

import sqlite3
import struct
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ChunkRow:
    id: int
    path: str
    start_line: int
    end_line: int
    text: str


SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY,
    content_hash TEXT NOT NULL,
    mtime REAL NOT NULL,
    verdict TEXT NOT NULL,      -- 'accepted' | 'rejected'
    reason TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    text TEXT NOT NULL,
    content_hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_path ON chunks(path);

-- Plain (non-contentless) FTS5 table: stores its own copy of the text.
-- Contentless (content='') tables require special 'delete' insert commands
-- instead of plain DELETE/rowid semantics, which complicates eviction for
-- no real benefit at this scale -- simplicity here wins over the small
-- storage duplication.
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    text
);

CREATE TABLE IF NOT EXISTS vectors (
    chunk_id INTEGER PRIMARY KEY,
    embedding BLOB NOT NULL,
    dim INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _pack(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def _unpack(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


class Store:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.path = db_path
        self.conn = sqlite3.connect(str(db_path))
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # ---- fingerprint -----------------------------------------------------

    def get_fingerprint(self) -> tuple[str, int] | None:
        row = self.conn.execute(
            "SELECT value FROM meta WHERE key = 'embedding_model'"
        ).fetchone()
        dim_row = self.conn.execute(
            "SELECT value FROM meta WHERE key = 'embedding_dim'"
        ).fetchone()
        if not row or not dim_row:
            return None
        return row[0], int(dim_row[0])

    def set_fingerprint(self, model_name: str, dim: int) -> None:
        self.conn.execute(
            "INSERT INTO meta(key, value) VALUES ('embedding_model', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (model_name,),
        )
        self.conn.execute(
            "INSERT INTO meta(key, value) VALUES ('embedding_dim', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(dim),),
        )
        self.conn.commit()

    # ---- files -------------------------------------------------------

    def get_file_hash(self, path: str) -> str | None:
        row = self.conn.execute("SELECT content_hash FROM files WHERE path = ?", (path,)).fetchone()
        return row[0] if row else None

    def upsert_file(self, path: str, content_hash: str, mtime: float, verdict: str, reason: str) -> None:
        self.conn.execute(
            "INSERT INTO files(path, content_hash, mtime, verdict, reason) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(path) DO UPDATE SET content_hash=excluded.content_hash, "
            "mtime=excluded.mtime, verdict=excluded.verdict, reason=excluded.reason",
            (path, content_hash, mtime, verdict, reason),
        )

    def all_known_paths(self) -> set[str]:
        return {row[0] for row in self.conn.execute("SELECT path FROM files")}

    # ---- chunks / eviction ------------------------------------------------

    def evict_path(self, path: str) -> int:
        """Purge every trace of `path` from chunks, fts, and vectors. Returns
        the number of chunks removed."""
        ids = [r[0] for r in self.conn.execute("SELECT id FROM chunks WHERE path = ?", (path,))]
        for cid in ids:
            self.conn.execute("DELETE FROM chunks_fts WHERE rowid = ?", (cid,))
            self.conn.execute("DELETE FROM vectors WHERE chunk_id = ?", (cid,))
        self.conn.execute("DELETE FROM chunks WHERE path = ?", (path,))
        self.conn.execute("DELETE FROM files WHERE path = ?", (path,))
        return len(ids)

    def replace_chunks(
        self,
        path: str,
        chunks: list[tuple[int, int, str, str]],  # (start_line, end_line, text, content_hash)
        embeddings: list[list[float]] | None,
    ) -> None:
        """Delete this path's existing chunks and insert the new set."""
        self.evict_chunks_only(path)
        for idx, (start, end, text, content_hash) in enumerate(chunks):
            cur = self.conn.execute(
                "INSERT INTO chunks(path, start_line, end_line, text, content_hash) VALUES (?, ?, ?, ?, ?)",
                (path, start, end, text, content_hash),
            )
            chunk_id = cur.lastrowid
            self.conn.execute("INSERT INTO chunks_fts(rowid, text) VALUES (?, ?)", (chunk_id, text))
            if embeddings is not None:
                vec = embeddings[idx]
                self.conn.execute(
                    "INSERT INTO vectors(chunk_id, embedding, dim) VALUES (?, ?, ?)",
                    (chunk_id, _pack(vec), len(vec)),
                )

    def evict_chunks_only(self, path: str) -> None:
        """Like evict_path but leaves the `files` row alone -- used when
        re-chunking a file that's still accepted, as opposed to a file that
        transitioned to rejected."""
        ids = [r[0] for r in self.conn.execute("SELECT id FROM chunks WHERE path = ?", (path,))]
        for cid in ids:
            self.conn.execute("DELETE FROM chunks_fts WHERE rowid = ?", (cid,))
            self.conn.execute("DELETE FROM vectors WHERE chunk_id = ?", (cid,))
        self.conn.execute("DELETE FROM chunks WHERE path = ?", (path,))

    def commit(self) -> None:
        self.conn.commit()

    # ---- search -------------------------------------------------------

    def search_fts(self, query: str, limit: int, paths: list[str] | None = None) -> list[ChunkRow]:
        """
        Full-text candidate generation. `paths` narrows to an explicit set of
        paths (e.g. for a within-repo sub-scope); paths=[] matches nothing,
        not "no filter" -- an empty scope is a real empty scope, never
        silently disabled.
        """
        if paths is not None and len(paths) == 0:
            return []

        sql = (
            "SELECT c.id, c.path, c.start_line, c.end_line, c.text "
            "FROM chunks_fts f JOIN chunks c ON c.id = f.rowid "
            "WHERE chunks_fts MATCH ?"
        )
        params: list = [query]
        if paths is not None:
            placeholders = ",".join("?" for _ in paths)
            sql += f" AND c.path IN ({placeholders})"
            params.extend(paths)
        sql += " ORDER BY bm25(chunks_fts) LIMIT ?"
        params.append(limit)

        rows = self.conn.execute(sql, params).fetchall()
        return [ChunkRow(id=r[0], path=r[1], start_line=r[2], end_line=r[3], text=r[4]) for r in rows]

    def get_vectors(self, chunk_ids: list[int]) -> dict[int, list[float]]:
        if not chunk_ids:
            return {}
        placeholders = ",".join("?" for _ in chunk_ids)
        rows = self.conn.execute(
            f"SELECT chunk_id, embedding FROM vectors WHERE chunk_id IN ({placeholders})",
            chunk_ids,
        ).fetchall()
        return {cid: _unpack(blob) for cid, blob in rows}

    # ---- reset -------------------------------------------------------

    def reset(self, *, include_meta: bool = False) -> None:
        """Clear files, chunks, chunks_fts, and vectors. Meta (embedding
        fingerprint) survives by default since it describes the embedding
        backend, not indexed content -- pass include_meta=True for a truly
        full wipe."""
        self.conn.execute("DELETE FROM files")
        self.conn.execute("DELETE FROM chunks")
        self.conn.execute("DELETE FROM chunks_fts")
        self.conn.execute("DELETE FROM vectors")
        if include_meta:
            self.conn.execute("DELETE FROM meta")
        self.conn.commit()
