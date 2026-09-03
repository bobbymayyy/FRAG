"""
Dependency-free fallback chunker. Splits a file into overlapping line-range
windows. This is the baseline every repo gets regardless of installed
extras; a symbol-aware chunker (tree-sitter, capability extra "symbols")
would register into the same "chunker" slot at higher priority and, if it
constructs successfully, take over -- but that plugin must validate
tree-sitter is actually importable in its own __init__, eagerly, not fall
back to line-chunking internally while claiming to be active.
"""

from __future__ import annotations

from dataclasses import dataclass

from frag.registry import REGISTRY

CHUNK_LINES = 60
OVERLAP_LINES = 10


@dataclass
class Chunk:
    start_line: int  # 1-indexed, inclusive
    end_line: int     # 1-indexed, inclusive
    text: str


class LineChunker:
    name = "line"

    def __init__(self) -> None:
        pass  # no dependencies; always constructs

    def chunk(self, text: str) -> list[Chunk]:
        lines = text.splitlines()
        if not lines:
            return []
        chunks: list[Chunk] = []
        step = CHUNK_LINES - OVERLAP_LINES
        i = 0
        while i < len(lines):
            window = lines[i : i + CHUNK_LINES]
            start = i + 1
            end = i + len(window)
            chunks.append(Chunk(start_line=start, end_line=end, text="\n".join(window)))
            if end >= len(lines):
                break
            i += step
        return chunks


REGISTRY.register("chunker", "line", LineChunker, priority=0)
