"use client";

import { useQuery } from "@tanstack/react-query";
import { useDebounce } from "use-debounce";

import { wikiKeys } from "@/lib/wiki-keys";
import { WIKI_READ_API } from "@/lib/wiki-read-api";
import type { DriveSearchResult, SnippetResult } from "@/types/wiki";

export function useDriveSearch(query: string, enabled = true) {
  const [debouncedQuery] = useDebounce(query.trim(), 250);
  return useQuery<DriveSearchResult[]>({
    enabled: enabled && debouncedQuery.length >= 2 && debouncedQuery.length <= 2048,

    queryFn: async ({ signal }) => await WIKI_READ_API.driveSearch(debouncedQuery, 10, signal),
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
    enabled: enabled && !!fileId && query.length >= 2 && query.length <= 2048,
    gcTime: 10 * 60 * 1000,
    queryFn: async ({ signal }) =>
      await WIKI_READ_API.driveSearchSnippet(fileId ?? "", query, mimeType, signal),

    queryKey: fileId
      ? wikiKeys.driveSearchSnippet(fileId, query)
      : wikiKeys.driveSearchSnippet("disabled", ""),
    staleTime: 5 * 60 * 1000,
  });
}
