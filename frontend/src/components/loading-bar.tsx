"use client";

import { useIsFetching, useIsMutating } from "@tanstack/react-query";

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
