"use client";

import { useIsFetching, useIsMutating } from "@tanstack/react-query";

/**
 * Top-of-viewport progress bar driven by TanStack Query state.
 *
 * Mount once inside the ``QueryClientProvider``. Shows for:
 *   * any in-flight mutation (the user is waiting for a write)
 *   * any FOREGROUND fetch — queries whose cache is empty so the UI
 *     genuinely has nothing to render yet
 *
 * Background refetches (cache populated, just revalidating after an
 * `invalidateQueries`) are intentionally excluded. Otherwise every
 * optimistic mutation kicks off a full re-walk of the drive tree, the
 * fetch-count stays nonzero for seconds after the UI already painted
 * the change, and the bar reads as "this is still happening" when
 * from the user's perspective it isn't.
 */
export function GlobalLoadingBar() {
  const foregroundFetching = useIsFetching({
    predicate: (query) => query.state.data === undefined,
  });
  const mutating = useIsMutating();
  if (foregroundFetching === 0 && mutating === 0) return null;
  return (
    <div className="fixed top-0 right-0 left-0 z-[9999] h-0.5">
      <div className="bg-primary animate-loading-bar h-full" />
    </div>
  );
}
