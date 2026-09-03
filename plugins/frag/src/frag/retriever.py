"""
Two-stage retrieval. This is the actual point of FRAG: given a free-text
symptom report ("noticing X behavior, need Y instead"), return the smallest
set of fragments worth sending for review -- not the whole repo, not even
the whole file.

Stage 1 (always available, zero deps): FTS5 MATCH over chunk text, broad
recall -- cast a wide net cheaply.

Stage 2 (only if an embedder is active and fingerprint-consistent): cosine
re-rank of the stage-1 candidates against the query embedding, narrowing to
the top_k most semantically relevant fragments. If no embedder is available,
stage 1's bm25 ordering is the final ordering -- correct, just less precise.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from frag.embedder import try_get_embedder
from frag.store import Store

CANDIDATE_POOL = 50


@dataclass
class Fragment:
    path: str
    start_line: int
    end_line: int
    text: str
    score: float


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def search(store: Store, query: str, top_k: int = 8, paths: list[str] | None = None) -> list[Fragment]:
    candidates = store.search_fts(query, limit=CANDIDATE_POOL, paths=paths)
    if not candidates:
        return []

    embedder = try_get_embedder()
    fingerprint = store.get_fingerprint()
    if embedder is None or fingerprint is None or fingerprint != (embedder.name, embedder.dim):
        # Lexical-only: bm25 order from stage 1 is final, just truncate.
        return [
            Fragment(path=c.path, start_line=c.start_line, end_line=c.end_line, text=c.text, score=1.0 / (i + 1))
            for i, c in enumerate(candidates[:top_k])
        ]

    chunk_vectors = store.get_vectors([c.id for c in candidates])
    if not chunk_vectors:
        return [
            Fragment(path=c.path, start_line=c.start_line, end_line=c.end_line, text=c.text, score=1.0 / (i + 1))
            for i, c in enumerate(candidates[:top_k])
        ]

    query_vec = embedder.embed([query])[0]
    scored = []
    for c in candidates:
        vec = chunk_vectors.get(c.id)
        score = _cosine(query_vec, vec) if vec is not None else 0.0
        scored.append((score, c))
    scored.sort(key=lambda pair: pair[0], reverse=True)

    return [
        Fragment(path=c.path, start_line=c.start_line, end_line=c.end_line, text=c.text, score=score)
        for score, c in scored[:top_k]
    ]
