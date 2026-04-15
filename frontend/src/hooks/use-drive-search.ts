"use client";

import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { navigatorApi } from "@/lib/navigator-api";
import type { DriveSearchResult, SnippetResult } from "@/types/navigator";

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

/**
 * Lazy snippet fetch for a single file + query combo. Used by the
 * command palette preview panel — only the currently-highlighted
 * result triggers a fetch, and React Query caches by (fileId, query)
 * so re-selecting is instant. Snippets are expensive (1 Drive export
 * per call) so keep the cache time long.
 */
export function useDriveSearchSnippet(fileId: string | null, query: string, enabled: boolean) {
  return useQuery<SnippetResult>({
    queryKey: fileId
      ? ["navigator", "drive-search", "snippet", fileId, query]
      : ["navigator", "drive-search", "snippet", "disabled"],
    queryFn: () => navigatorApi.driveSearchSnippet(fileId ?? "", query),
    enabled: enabled && !!fileId && query.length >= 2,
    // Snippets are deterministic for a given (file, query) pair until
    // the file's content changes. Cache for 5 minutes so arrowing up
    // and down through results is instant after the first pass.
    staleTime: 5 * 60 * 1000,
    gcTime: 10 * 60 * 1000,
  });
}
