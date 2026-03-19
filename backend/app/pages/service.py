"""Backward-compatible re-exports from the split pages modules.

All public functions that were originally in this monolithic service.py
are now in crud.py, content.py, background.py, tree.py, and sync.py.
This file re-exports them so any existing ``from app.pages.service import X``
or ``from app.pages import service; service.X`` still works.
"""

from app.pages.background import (  # noqa: F401
    finalize_page_creation,
    move_page_in_drive,
    retry_pending_page,
)
from app.pages.content import (  # noqa: F401
    get_page_content,
)
from app.pages.crud import (  # noqa: F401
    create_page,
    delete_page,
    get_page,
    publish_page,
    update_page,
)
from app.pages.sync import (  # noqa: F401
    sync_drive_state,
)
from app.pages.tree import (  # noqa: F401
    get_page_tree,
)
