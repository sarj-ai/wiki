"use client";

import { useQuery } from "@tanstack/react-query";
import { useDebounce } from "use-debounce";

import { wikiKeys } from "@/lib/wiki-keys";
import { WIKI_READ_API } from "@/lib/wiki-read-api";
import {
  type DriveSearchResult,
  MIME_DOC,
  MIME_SHEET,
  MIME_SLIDE,
  type SnippetResult,
} from "@/types/wiki";

const SEARCH_GC_TIME_MS = 10 * 60 * 1000;
const SEARCH_DEBOUNCE_MS = 250;

export function useDriveSearchState(query: string, enabled = true) {
  const currentQuery = query.trim();
  const [debouncedQuery] = useDebounce(currentQuery, SEARCH_DEBOUNCE_MS);
  const result = useQuery<DriveSearchResult[]>({
    enabled:
      enabled &&
      currentQuery === debouncedQuery &&
      currentQuery.length >= 2 &&
      currentQuery.length <= 2048,
    gcTime: SEARCH_GC_TIME_MS,
    queryFn: async ({ signal }) => await WIKI_READ_API.driveSearch(debouncedQuery, 10, signal),
    queryKey: wikiKeys.driveSearch(debouncedQuery),

    staleTime: 30_000,
  });
  return { query: result, isCurrentQuery: currentQuery === debouncedQuery };
}

export function useDriveSearchSnippet(
  fileId: null | string,
  query: string,
  enabled: boolean,
  mimeType?: string,
) {
  const [debouncedQuery] = useDebounce(query, SEARCH_DEBOUNCE_MS);
  const supportsSnippet =
    !mimeType || mimeType === MIME_DOC || mimeType === MIME_SHEET || mimeType === MIME_SLIDE;
  return useQuery<SnippetResult>({
    enabled:
      enabled &&
      !!fileId &&
      supportsSnippet &&
      query === debouncedQuery &&
      query.length >= 2 &&
      query.length <= 2048,
    gcTime: SEARCH_GC_TIME_MS,
    queryFn: async ({ signal }) =>
      await WIKI_READ_API.driveSearchSnippet(fileId ?? "", debouncedQuery, mimeType, signal),

    queryKey: fileId
      ? wikiKeys.driveSearchSnippet(fileId, debouncedQuery)
      : wikiKeys.driveSearchSnippet("disabled", ""),
    staleTime: 5 * 60 * 1000,
  });
}
