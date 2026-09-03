"""
Embedder slot. Nothing is registered here by default -- embeddings are a
capability extra (`semantic`), not a required runtime dependency. When
nothing is registered, REGISTRY.best("embedder") raises LookupError and the
Indexer/Retriever fall back to lexical-only (FTS5) operation, which is
always correct, just less precise.

A real embedder plugin (e.g. sentence-transformers-backed) lives outside
this module and registers itself into REGISTRY at slot "embedder" only if
its dependency actually imports successfully in its own __init__.
"""

from __future__ import annotations

from typing import Protocol


class Embedder(Protocol):
    name: str   # model identifier, used in the fingerprint
    dim: int    # embedding dimension, used in the fingerprint

    def embed(self, texts: list[str]) -> list[list[float]]:
        ...


def try_get_embedder():
    """Returns an Embedder instance, or None if no embedder plugin is
    available/constructible. Never raises."""
    from frag.registry import REGISTRY

    try:
        return REGISTRY.best("embedder")
    except (LookupError, RuntimeError):
        return None
