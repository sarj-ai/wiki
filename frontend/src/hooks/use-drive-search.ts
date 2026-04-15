"use client";

import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { navigatorApi } from "@/lib/navigator-api";
import type { DriveSearchResult } from "@/types/navigator";

// Small local debounce so the query only fires ~250ms after the user
// stops typing. Keeps the Drive Search API call count sane without
// pulling in a full debounce library.
function useDebounced<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const handle = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(handle);
  }, [value, delayMs]);
  return debounced;
}

export function useDriveSearch(query: string) {
  const debouncedQuery = useDebounced(query.trim(), 250);
  return useQuery<DriveSearchResult[]>({
    queryKey: ["navigator", "drive-search", debouncedQuery],
    // Drive Search API fan-out happens server-side across every drive
    // the user has access to (see backend/app/drive/search_router.py).
    queryFn: () => navigatorApi.driveSearch(debouncedQuery, 10),
    enabled: debouncedQuery.length >= 2,
    // Results are cheap to refetch and stale quickly for queries like
    // "latest doc", so keep them fresh for ~30s only.
    staleTime: 30_000,
  });
}
