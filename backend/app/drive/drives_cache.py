"""Per-user TTL cache for ``drives.list``.

Drive membership barely changes during a normal session, but every
file mutation today calls ``drives.list`` (via ``_ensure_in_member_drive``)
to confirm the file's drive is still in the user's Navigator scope.
That round-trip dominates the latency of create / rename / move /
delete because it's serialised before the actual mutation.

This module memoises the result per user with a short TTL so the
scope check becomes O(1) within the window. Drive remains the auth
source of truth — within the cache window, a write whose target the
user has lost access to still hits Drive and Drive rejects with the
real 403, so we never grant a permission Drive wouldn't.

Invalidate() is hooked from ``AuthService.revoke_session`` so a user
who logs out can't have a leftover cache entry mislead a subsequent
session (and so the entry is reaped instead of leaking).
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from app.drive.schemas import DriveSummary

_TTL_SECONDS = 30.0


@dataclass(frozen=True)
class _Entry:
    drives: tuple[DriveSummary, ...]
    fetched_at: float


_entries: dict[str, _Entry] = {}


def lookup(user_id: str) -> list[DriveSummary] | None:
    """Return the cached drives for ``user_id`` if still fresh, else
    ``None``. Stale entries are not actively evicted on read — the
    caller refetches and overwrites via :func:`store`. This avoids
    holding any lock; the dict ops are atomic under the GIL and the
    worst-case race is two concurrent fetchers both writing the same
    fresh result.
    """
    entry = _entries.get(user_id)
    if entry is None:
        return None
    if time.monotonic() - entry.fetched_at >= _TTL_SECONDS:
        return None
    return list(entry.drives)


def store(user_id: str, drives: list[DriveSummary]) -> None:
    """Cache the freshly-fetched drives for ``user_id``. Tuples for the
    stored value are immutable so the cached list can't be accidentally
    mutated by a downstream consumer.
    """
    _entries[user_id] = _Entry(drives=tuple(drives), fetched_at=time.monotonic())


def invalidate(user_id: str) -> None:
    """Drop ``user_id``'s cache entry. Called on logout so the
    membership check on the user's next session starts cold instead
    of replaying a possibly-stale list.
    """
    _entries.pop(user_id, None)


def clear() -> None:
    """Drop every cache entry. Test-only — production code should use
    :func:`invalidate` keyed on a specific user.
    """
    _entries.clear()
