import { buildChildrenIndex } from "@/lib/wiki-tree";
import type { DocIcon, DriveNode, FolderIcon, SortOrder } from "@/types/wiki";

export const EMPTY_NODES: readonly DriveNode[] = [];
export const EMPTY_DOC_ICONS: readonly DocIcon[] = [];

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
