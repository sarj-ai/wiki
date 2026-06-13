"use client";

import { useQuery } from "@tanstack/react-query";
import { useDebounce } from "use-debounce";

import type { DriveSearchResult, SnippetResult } from "@/types/wiki";

import { wikiApi } from "@/lib/wiki-api";
import { wikiKeys } from "@/lib/wiki-keys";

export function useDriveSearch(query: string) {
  const [debouncedQuery] = useDebounce(query.trim(), 250);
  return useQuery<DriveSearchResult[]>({
    queryKey: wikiKeys.driveSearch(debouncedQuery),

    queryFn: () => wikiApi.driveSearch(debouncedQuery, 10),
    enabled: debouncedQuery.length >= 2 && debouncedQuery.length <= 2048,

    staleTime: 30_000,
  });
}

export function useDriveSearchSnippet(
  fileId: string | null,
  query: string,
  enabled: boolean,
  mimeType?: string,
) {
  return useQuery<SnippetResult>({
    queryKey: fileId
      ? wikiKeys.driveSearchSnippet(fileId, query)
      : wikiKeys.driveSearchSnippet("disabled", ""),
    queryFn: () => wikiApi.driveSearchSnippet(fileId ?? "", query, mimeType),
    enabled: enabled && !!fileId && query.length >= 2,

    staleTime: 5 * 60 * 1000,
    gcTime: 10 * 60 * 1000,
  });
}
