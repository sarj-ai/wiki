"""Per-user stale-while-revalidate cache for ``drives.list``.

Drive membership barely changes during a normal session, but every
file mutation today calls ``drives.list`` (via ``_ensure_in_member_drive``)
to confirm the file's drive is still in the user's Sarj Wiki scope.
That round-trip dominates the latency of create / rename / move /
delete because it's serialised before the actual mutation.

Caching strategy
----------------

* **Cold** (no entry): synchronous fetch, store result, return. The
  user's first op after login or a worker restart pays one network
  round-trip — unavoidable.
* **Fresh** (entry younger than ``_FRESH_SECONDS``): return cached
  immediately, no network. The hot path.
* **Stale but not expired** (entry between ``_FRESH_SECONDS`` and
  ``_HARD_EXPIRY_SECONDS``): return the stale value immediately AND
  kick off a background refetch (deduplicated per user) so the cache
  is current the next time anyone reads. Every op stays fast; cache
  silently catches up within one round-trip.
* **Hard-expired** (entry older than ``_HARD_EXPIRY_SECONDS``):
  treat as cold. Caps memory growth from one-time visitors and
  bounds the worst-case staleness window.

Drive remains the auth source of truth — the cache only memoises
the *scope* answer ("is this drive in the user's Sarj Wiki
surface"), not the *authorization* answer ("can the user mutate
this file"). A user who's lost write access to a drive still gets
Drive's real 403 on the next mutation; only the visible sidebar
lags by at most ``_FRESH_SECONDS``.

Invalidation is hooked from ``AuthService.revoke_session`` so a
logged-out session can't have a leftover cache entry mislead a
subsequent session on the same backend instance.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import time

from loguru import logger

from app.drive.schemas import DriveSummary


# Within this window the cached entry is treated as fresh and no
# background refetch is scheduled. Tuned to be generous — drive
# membership barely changes second-by-second, and keeping it small
# just thrashes the network for no UX benefit.
_FRESH_SECONDS = 30.0

# Beyond this, the cached entry is dropped instead of revalidated —
# bounds the worst-case staleness if a user goes silent for a long
# time, and keeps memory bounded for one-time visitors that never
# return.
_HARD_EXPIRY_SECONDS = 24 * 60 * 60.0  # 24h


@dataclass(frozen=True)
class _Entry:
    drives: tuple[DriveSummary, ...]
    fetched_at: float


_entries: dict[str, _Entry] = {}
# Set of user ids whose drives.list is currently being revalidated in
# the background — singleflight gate so a burst of concurrent stale
# reads doesn't fan out to N redundant Drive calls.
_revalidating: set[str] = set()
# asyncio.create_task return values must be referenced or the task is
# eligible for garbage collection mid-flight. Holding a strong ref in
# this set keeps the task alive until its done callback removes it.
_background_tasks: set[asyncio.Task[None]] = set()


async def get_drives(
    user_id: str,
    fetcher: Callable[[], Awaitable[list[DriveSummary]]],
) -> list[DriveSummary]:
    """Stale-while-revalidate accessor.

    ``fetcher`` is the raw call into Drive (typically a closure over
    ``DriveService._list_drives_sync`` via ``asyncio.to_thread``). The
    cache decides whether to invoke it synchronously, kick it off in
    the background, or skip it entirely.
    """
    entry = _entries.get(user_id)
    now = time.monotonic()

    if entry is None or now - entry.fetched_at >= _HARD_EXPIRY_SECONDS:
        drives = await fetcher()
        _entries[user_id] = _Entry(drives=tuple(drives), fetched_at=time.monotonic())
        return drives

    age = now - entry.fetched_at
    cached = list(entry.drives)
    if age < _FRESH_SECONDS:
        return cached

    # Stale: serve cached immediately, refresh in the background. The
    # singleflight gate keeps a flurry of stale reads from spawning N
    # concurrent Drive calls.
    if user_id not in _revalidating:
        _revalidating.add(user_id)
        _spawn_revalidation(user_id, fetcher)
    return cached


def invalidate(user_id: str) -> None:
    """Drop ``user_id``'s cache entry. Called on logout so the
    membership check on the user's next session starts cold instead
    of replaying a possibly-stale list. Any in-flight revalidation
    is left to settle (its result will land in the freshly-emptied
    slot, which is harmless — same data the next request would
    have fetched anyway).
    """
    _entries.pop(user_id, None)


def clear() -> None:
    """Drop every cache entry and revalidation marker. Test-only —
    production code should use :func:`invalidate` keyed on a user.
    """
    _entries.clear()
    _revalidating.clear()
    _background_tasks.clear()


def _spawn_revalidation(
    user_id: str,
    fetcher: Callable[[], Awaitable[list[DriveSummary]]],
) -> None:
    """Schedule a background refresh that updates the cache when it
    lands. Failures are swallowed (logged) so a transient Drive blip
    doesn't drop the cached value the request path is still serving.
    """

    async def _run() -> None:
        try:
            drives = await fetcher()
            _entries[user_id] = _Entry(drives=tuple(drives), fetched_at=time.monotonic())
        except Exception:  # noqa: BLE001 — background; log and move on.
            logger.opt(exception=True).warning(
                "drives.list revalidation failed for user={}", user_id
            )
        finally:
            _revalidating.discard(user_id)

    task = asyncio.create_task(_run())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
