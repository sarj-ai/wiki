from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import time

from loguru import logger

from app.drive.schemas import DriveSummary


_FRESH_SECONDS = 30.0

_HARD_EXPIRY_SECONDS = 24 * 60 * 60.0


@dataclass(frozen=True)
class _Entry:
    drives: tuple[DriveSummary, ...]
    fetched_at: float


_entries: dict[str, _Entry] = {}
_revalidating: set[str] = set()
_background_tasks: set[asyncio.Task[None]] = set()


async def get_drives(
    user_id: str,
    fetcher: Callable[[], Awaitable[list[DriveSummary]]],
) -> list[DriveSummary]:
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

    if user_id not in _revalidating:
        _revalidating.add(user_id)
        _spawn_revalidation(user_id, fetcher)
    return cached


def invalidate(user_id: str) -> None:
    _entries.pop(user_id, None)


def clear() -> None:
    _entries.clear()
    _revalidating.clear()
    _background_tasks.clear()


def _spawn_revalidation(
    user_id: str,
    fetcher: Callable[[], Awaitable[list[DriveSummary]]],
) -> None:

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
