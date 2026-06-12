from __future__ import annotations

from dataclasses import dataclass
import time

from app.drive.schemas import ShortcutProbe


_TTL_SECONDS = 300.0

_MAX_ENTRIES = 8192


@dataclass(frozen=True)
class _Entry:
    probe: ShortcutProbe | None
    fetched_at: float


_entries: dict[tuple[str, str], _Entry] = {}


def get_fresh(user_id: str, target_ids: list[str]) -> dict[str, ShortcutProbe | None]:
    now = time.monotonic()
    out: dict[str, ShortcutProbe | None] = {}
    for target_id in target_ids:
        entry = _entries.get((user_id, target_id))
        if entry is not None and now - entry.fetched_at < _TTL_SECONDS:
            out[target_id] = entry.probe
    return out


def store(user_id: str, probes: dict[str, ShortcutProbe | None]) -> None:
    if len(_entries) >= _MAX_ENTRIES:
        _prune_expired()
    now = time.monotonic()
    for target_id, probe in probes.items():
        _entries[(user_id, target_id)] = _Entry(probe=probe, fetched_at=now)


def clear() -> None:
    _entries.clear()


def _prune_expired() -> None:
    now = time.monotonic()
    expired = [key for key, entry in _entries.items() if now - entry.fetched_at >= _TTL_SECONDS]
    for key in expired:
        del _entries[key]
