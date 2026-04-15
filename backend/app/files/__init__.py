"""Unified Drive file CRUD for every Navigator-supported mime type.

The sidebar's "+" button, rename, move, and delete all flow through
this module. Every call uses the acting user's OAuth credentials —
no service account, ever.
"""

from app.files.router import router as files_router

__all__ = ["files_router"]
