"""
Plugin registry.

Modularity is a development seam, not a runtime setting. There is no config
file that lets a caller pick a plugin by name. Instead, each slot ("host",
"chunker", "embedder", ...) has zero or more registered factories with a
priority. REGISTRY.best(slot, *args, **kwargs) tries factories highest
priority first, actually constructs them (never lazily defers real
validation), and returns the first one that constructs without raising.

A plugin's __init__ must validate its own dependencies/env eagerly. If it
can't actually do its job, it must raise during construction so the registry
skips it -- not construct "successfully" and silently degrade later while
still reporting itself as active.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

log = logging.getLogger("frag.registry")


@dataclass(order=True)
class _Entry:
    priority: int
    name: str = field(compare=False)
    factory: Callable[..., Any] = field(compare=False)


class Registry:
    def __init__(self) -> None:
        self._slots: dict[str, list[_Entry]] = {}

    def register(self, slot: str, name: str, factory: Callable[..., Any], priority: int = 0) -> None:
        self._slots.setdefault(slot, []).append(_Entry(priority=priority, name=name, factory=factory))
        self._slots[slot].sort(key=lambda e: e.priority, reverse=True)

    def best(self, slot: str, *args: Any, **kwargs: Any) -> Any:
        entries = self._slots.get(slot, [])
        if not entries:
            raise LookupError(f"no plugins registered for slot {slot!r}")
        last_err: Exception | None = None
        for entry in entries:
            try:
                instance = entry.factory(*args, **kwargs)
                log.info("slot=%s settled on plugin=%s", slot, entry.name)
                return instance
            except Exception as exc:  # noqa: BLE001 - intentionally broad, this is a fallback chain
                log.info("slot=%s skipping plugin=%s reason=%r", slot, entry.name, exc)
                last_err = exc
        raise RuntimeError(f"no plugin for slot {slot!r} could construct") from last_err

    def all_registered(self, slot: str) -> list[str]:
        return [e.name for e in self._slots.get(slot, [])]


REGISTRY = Registry()
