"use client";

import { useQuery } from "@tanstack/react-query";
import { useDebounce } from "use-debounce";

import { wikiKeys } from "@/lib/wiki-keys";
import { wikiReadApi } from "@/lib/wiki-read-client";
import type { DriveSearchResult, SnippetResult } from "@/types/wiki";

export function useDriveSearch(query: string) {
  const [debouncedQuery] = useDebounce(query.trim(), 250);
  return useQuery<DriveSearchResult[]>({
    enabled: debouncedQuery.length >= 2 && debouncedQuery.length <= 2048,

    queryFn: async () => await wikiReadApi.driveSearch(debouncedQuery, 10),
    queryKey: wikiKeys.driveSearch(debouncedQuery),

    staleTime: 30_000,
  });
}

export function useDriveSearchSnippet(
  fileId: null | string,
  query: string,
  enabled: boolean,
  mimeType?: string,
) {
  return useQuery<SnippetResult>({
    enabled: enabled && !!fileId && query.length >= 2,
    gcTime: 10 * 60 * 1000,
    queryFn: async () => await wikiReadApi.driveSearchSnippet(fileId ?? "", query, mimeType),

    queryKey: fileId
      ? wikiKeys.driveSearchSnippet(fileId, query)
      : wikiKeys.driveSearchSnippet("disabled", ""),
    staleTime: 5 * 60 * 1000,
  });
}
