"""Drive API wrappers used by the Navigator.

All calls are user-scoped — no service account is involved in the
read or write path (PIVOT_PLAN.md §8.2). Modules:

- credentials: builds a Drive v3 service from a user's encrypted OAuth
  tokens
- drives: ``drives.list()`` wrapper, also the access-control boundary
- tree_walker: folder-tree walk + lazy folder child listing
- search: Drive Search API per drive + fan-out across drives
- schemas: Pydantic DTOs for cache + API payloads
"""

from app.drive.credentials import build_credentials, get_drive_service

__all__ = ["build_credentials", "get_drive_service"]
