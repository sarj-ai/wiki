"use client";

import { useQuery } from "@tanstack/react-query";
import { useDebounce } from "use-debounce";

import type { DriveSearchResult, SnippetResult } from "@/types/wiki";

import { wikiApi } from "@/lib/wiki-api";

export function useDriveSearch(query: string) {
  // ~250ms debounce keeps the Drive Search API call count sane while
  // typing, without the dance of a hand-rolled timeout effect.
  const [debouncedQuery] = useDebounce(query.trim(), 250);
  return useQuery<DriveSearchResult[]>({
    queryKey: ["wiki", "drive-search", debouncedQuery],
    // Drive Search API fan-out happens server-side across every drive
    // the user has access to (see backend/app/drive/search_router.py).
    queryFn: () => wikiApi.driveSearch(debouncedQuery, 10),
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
export function useDriveSearchSnippet(
  fileId: string | null,
  query: string,
  enabled: boolean,
  mimeType?: string,
) {
  return useQuery<SnippetResult>({
    queryKey: fileId
      ? ["wiki", "drive-search", "snippet", fileId, query]
      : ["wiki", "drive-search", "snippet", "disabled"],
    queryFn: () => wikiApi.driveSearchSnippet(fileId ?? "", query, mimeType),
    enabled: enabled && !!fileId && query.length >= 2,
    // Snippets are deterministic for a given (file, query) pair until
    // the file's content changes. Cache for 5 minutes so arrowing up
    // and down through results is instant after the first pass.
    staleTime: 5 * 60 * 1000,
    gcTime: 10 * 60 * 1000,
  });
}
