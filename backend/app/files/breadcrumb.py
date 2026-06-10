"""Pure breadcrumb walker for ``GET /files/{id}``.

Reads the cached ``DriveTree`` and chains parent-folder hops up from
the file's immediate parent to the drive root. Pure function — no
I/O — so it stays trivially testable and the caller (router) decides
where the tree comes from.
"""

from __future__ import annotations

from app.drive.schemas import DriveTree
from app.files.schemas import BreadcrumbItem


# Folder nesting beyond this depth is almost certainly a cycle in the
# tree we shouldn't walk. Matches the safety cap the old client-side
# walker carried.
_MAX_DEPTH = 64


def build_breadcrumb(
    tree: DriveTree | None,
    drive_id: str | None,
    drive_name: str | None,
    parent_folder_id: str | None,
) -> list[BreadcrumbItem]:
    chain: list[BreadcrumbItem] = []
    if tree is not None:
        by_id = {n.id: n for n in tree.folders}
        current = parent_folder_id
        depth = 0
        while current and depth < _MAX_DEPTH:
            node = by_id.get(current)
            if node is None:
                break
            chain.insert(0, BreadcrumbItem(id=node.id, name=node.name))
            current = node.parent_id
            depth += 1
    if drive_id and drive_name:
        chain.insert(0, BreadcrumbItem(id=drive_id, name=drive_name))
    return chain
