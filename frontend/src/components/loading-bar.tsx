"use client";

import { useIsFetching, useIsMutating } from "@tanstack/react-query";

/**
 * Top-of-viewport progress bar driven by TanStack Query state.
 *
 * Displays whenever any query is fetching or any mutation is in flight,
 * so callers don't need to hand-roll start/done bookkeeping. Mount once
 * inside the ``QueryClientProvider``.
 */
export function GlobalLoadingBar() {
  const fetching = useIsFetching();
  const mutating = useIsMutating();
  if (fetching === 0 && mutating === 0) return null;
  return (
    <div className="fixed top-0 right-0 left-0 z-[9999] h-0.5">
      <div className="bg-primary animate-loading-bar h-full" />
    </div>
  );
}
