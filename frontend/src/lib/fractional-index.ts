// Picks a new sort_order value for an item moved into position `newIndex`
// inside an ordered sibling list. Returns a number strictly between the
// item's new neighbors so only the moved item needs to be written.
//
// The `orderedItems` array is the sibling list BEFORE the move. Items
// may or may not have existing sort_order overrides — the callers join
// user_sort_order with Drive's default alphabetical order ahead of time
// and hand us a sorted list.
//
// We assume the caller also maintains a mapping `sortOrderOf(id)`; if a
// neighbor has no explicit override yet we fall back to its integer
// index so the returned midpoint still produces a stable ordering.
// Callers should treat the return value as "the new sort_order for the
// moved item, to be persisted via bulkSetSortOrders".
export function fractionalIndexFor(
  orderedItems: readonly string[],
  newIndex: number,
  movedId: string,
  sortOrderOf: (id: string) => number | undefined,
): number {
  // Build a neighbor list that excludes the moved item so we can read
  // the "before" and "after" positions it's being inserted between.
  const withoutMoved = orderedItems.filter((id) => id !== movedId);
  const clampedIndex = Math.max(0, Math.min(newIndex, withoutMoved.length));

  const beforeId = clampedIndex > 0 ? withoutMoved[clampedIndex - 1] : undefined;
  const afterId = clampedIndex < withoutMoved.length ? withoutMoved[clampedIndex] : undefined;

  const beforeValue = beforeId
    ? (sortOrderOf(beforeId) ?? withoutMoved.indexOf(beforeId))
    : undefined;
  const afterValue = afterId ? (sortOrderOf(afterId) ?? withoutMoved.indexOf(afterId)) : undefined;

  if (beforeValue !== undefined && afterValue !== undefined) {
    return (beforeValue + afterValue) / 2;
  }
  if (beforeValue !== undefined) return beforeValue + 1;
  if (afterValue !== undefined) return afterValue - 1;
  return 0;
}
