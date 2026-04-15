"""Navigator UI overlays: icons, sort order, user preferences.

These are the cheap per-entity knobs that live in our DB because they
can't live in Drive. Nothing here is authoritative; wiping every table
in this package rebuilds to defaults without data loss.
"""

from app.extras.router import router as extras_router

__all__ = ["extras_router"]
