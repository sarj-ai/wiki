"""Short-TTL cache for shortcut-target probe outcomes.

``_resolve_shortcuts`` probes every shortcut's target with a
``files.get`` batch on **every** tree walk and folder listing — the
probes were the dominant repeat cost of refreshing a shortcut-heavy
drive because nothing remembered the answer between requests.
Target accessibility barely changes second-by-second, so a short
per-user memo keeps repeat walks cheap without weakening the access
model: Drive's ACL on the target remains the source of truth, and a
permission change shows up after at most ``_TTL_SECONDS``.

Negative outcomes (``None`` — target deleted/trashed, or the probe
batch failed) are cached under the same TTL. That means a transient
batch failure can hide a shortcut for up to one TTL window, which is
an acceptable trade for not re-probing known-dead targets on every
walk.

Entries are keyed ``(user_id, target_id)`` — probes run under the
user's own OAuth token, so one user's answer must never serve
another's. Memory stays bounded by an opportunistic sweep of expired
entries whenever the map grows past ``_MAX_ENTRIES``.
"""

from __future__ import annotations

from dataclasses import dataclass
import time

from app.drive.schemas import ShortcutProbe


# Matches the staleness the UI already tolerates elsewhere (the
# drive-tree cache serves stale trees while revalidating): a revoked
# target renders clickable for at most this long before greying out.
_TTL_SECONDS = 300.0

# Sweep threshold, not a hard cap — generous for an org-internal tool
# (entries are ~100 bytes; this is a few MB worst case).
_MAX_ENTRIES = 8192


@dataclass(frozen=True)
class _Entry:
    probe: ShortcutProbe | None
    fetched_at: float


_entries: dict[tuple[str, str], _Entry] = {}


def get_fresh(user_id: str, target_ids: list[str]) -> dict[str, ShortcutProbe | None]:
    """Return the cached probe for every ``target_id`` still inside its
    TTL window. Missing/expired ids are simply absent from the result —
    the caller probes those and feeds the answers back via :func:`store`.
    """
    now = time.monotonic()
    out: dict[str, ShortcutProbe | None] = {}
    for target_id in target_ids:
        entry = _entries.get((user_id, target_id))
        if entry is not None and now - entry.fetched_at < _TTL_SECONDS:
            out[target_id] = entry.probe
    return out


def store(user_id: str, probes: dict[str, ShortcutProbe | None]) -> None:
    """Memoise freshly probed outcomes (including negative ones)."""
    if len(_entries) >= _MAX_ENTRIES:
        _prune_expired()
    now = time.monotonic()
    for target_id, probe in probes.items():
        _entries[(user_id, target_id)] = _Entry(probe=probe, fetched_at=now)


def clear() -> None:
    """Drop every cached probe. Test-only."""
    _entries.clear()


def _prune_expired() -> None:
    now = time.monotonic()
    expired = [key for key, entry in _entries.items() if now - entry.fetched_at >= _TTL_SECONDS]
    for key in expired:
        del _entries[key]
