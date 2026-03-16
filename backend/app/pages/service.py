"""Backward-compatible re-exports from the split pages modules.

All public functions that were originally in this monolithic service.py
are now in crud.py, tree.py, and sync.py.  This file re-exports them
so any existing ``from app.pages.service import X`` or
``from app.pages import service; service.X`` still works.
"""

from app.pages.crud import (  # noqa: F401
    create_page,
    delete_page,
    finalize_page_creation,
    get_page,
    get_page_content,
    move_page_in_drive,
    publish_page,
    retry_pending_page,
    update_page,
)
from app.pages.sync import (  # noqa: F401
    sync_drive_state,
)
from app.pages.tree import (  # noqa: F401
    get_page_tree,
)
