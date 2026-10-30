import { buildChildrenIndex, isFolderLikeNode } from "@/lib/wiki-tree";
import type { DocIcon, DriveNode, FolderIcon, SortOrder } from "@/types/wiki";
import { MIME_DOC, MIME_HTML, MIME_SHORTCUT } from "@/types/wiki";

export const EMPTY_NODES: readonly DriveNode[] = [];
export const EMPTY_DOC_ICONS: readonly DocIcon[] = [];
export const EMPTY_FOLDER_ICONS: readonly FolderIcon[] = [];
export const EMPTY_SORT_ORDERS: readonly SortOrder[] = [];

// Query data is immutable. Weak keys let every Drive share the same index while
// session/query eviction releases the arrays and their indexes together.
function memoizeIndex<T, V>(build: (items: readonly T[]) => V): (items: readonly T[]) => V {
  const cache = new WeakMap<readonly T[], V>();
  return (items) => {
    const cached = cache.get(items);
    if (cached !== undefined) return cached;
    const index = build(items);
    cache.set(items, index);
    return index;
  };
}

function mapByKey<T, V>(
  items: readonly T[],
  key: (item: T) => string,
  value: (item: T) => V,
): Map<string, V> {
  const map = new Map<string, V>();
  for (const item of items) map.set(key(item), value(item));
  return map;
}

export const indexChildren = memoizeIndex(buildChildrenIndex);
export const indexNodes = memoizeIndex((items: readonly DriveNode[]) =>
  mapByKey(
    items,
    (item) => item.id,
    (item) => item,
  ),
);
export const indexDestinationFolders = memoizeIndex((items: readonly DriveNode[]) =>
  buildChildrenIndex(items, MIME_SHORTCUT),
);
export const indexSidebarNodes = memoizeIndex((items: readonly DriveNode[]) => {
  const files: DriveNode[] = [];
  const folders: DriveNode[] = [];
  for (const node of items) {
    if (isFolderLikeNode(node)) folders.push(node);
    else files.push(node);
  }
  return { files, folders };
});
export const indexVisibleDocuments = memoizeIndex((items: readonly DriveNode[]) =>
  items.filter((node) => node.mimeType === MIME_DOC || node.mimeType === MIME_HTML),
);
export const indexFolderIcons = memoizeIndex((items: readonly FolderIcon[]) =>
  mapByKey(
    items,
    (item) => item.folderDriveId,
    (item) => item.icon,
  ),
);
export const indexDocIcons = memoizeIndex((items: readonly DocIcon[]) =>
  mapByKey(
    items,
    (item) => item.driveId,
    (item) => item.icon,
  ),
);
export const indexSortOrders = memoizeIndex((items: readonly SortOrder[]) =>
  mapByKey(
    items,
    (item) => item.driveId,
    (item) => item.sortOrder,
  ),
);
