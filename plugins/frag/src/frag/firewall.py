"""
Content firewall: decides whether a file's bytes are safe/sane to chunk and
surface to an LLM at all. This runs before chunking, on every file touched
by a sync -- not just at first index. A file that used to pass and now fails
(binary, newly gitignored, whatever) must be evicted, not left stale.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

DENY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".tiff",
    ".mp3", ".mp4", ".mov", ".avi", ".mkv", ".wav", ".flac",
    ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".so", ".dll", ".dylib", ".exe", ".bin", ".o", ".a", ".class", ".jar",
    ".sqlite", ".db",
    ".pyc", ".pyo",
}

DENY_PATH_SEGMENTS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    "dist", "build", ".frag",
}

# Common magic bytes for formats that might sneak in under an unrelated extension.
_MAGIC_SIGNATURES = [
    b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"PK\x03\x04",
    b"%PDF", b"\x7fELF", b"MZ",
]

MAX_FILE_BYTES = 2_000_000  # 2MB -- past this it's almost certainly not something worth chunking whole
ENTROPY_THRESHOLD = 7.2      # bits/byte; typical source text sits well under this


@dataclass
class Verdict:
    accepted: bool
    reason: str


def _shannon_entropy(data: bytes) -> float:
    if not data:
        return 0.0
    counts = Counter(data)
    length = len(data)
    return -sum((c / length) * math.log2(c / length) for c in counts.values())


def check(path: Path, *, repo_root: Path) -> Verdict:
    rel_parts = path.relative_to(repo_root).parts
    if any(seg in DENY_PATH_SEGMENTS for seg in rel_parts):
        return Verdict(False, "denied path segment")

    if path.suffix.lower() in DENY_EXTENSIONS:
        return Verdict(False, "denied extension")

    try:
        size = path.stat().st_size
    except OSError as exc:
        return Verdict(False, f"stat failed: {exc}")

    if size == 0:
        return Verdict(False, "empty file")
    if size > MAX_FILE_BYTES:
        return Verdict(False, "exceeds max file size")

    try:
        with path.open("rb") as fh:
            head = fh.read(4096)
    except OSError as exc:
        return Verdict(False, f"read failed: {exc}")

    if any(head.startswith(sig) for sig in _MAGIC_SIGNATURES):
        return Verdict(False, "binary magic bytes")

    if b"\x00" in head:
        return Verdict(False, "contains NUL byte")

    try:
        head.decode("utf-8")
    except UnicodeDecodeError:
        return Verdict(False, "not valid UTF-8")

    if _shannon_entropy(head) > ENTROPY_THRESHOLD:
        return Verdict(False, "entropy too high (likely binary/secret blob)")

    return Verdict(True, "accepted")
