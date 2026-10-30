import { CancelledError, type QueryClient } from "@tanstack/react-query";

import { fileQueryOptions } from "@/hooks/use-wiki-tree";
import { isAbortError } from "@/lib/mutation-toast";
import type { FileDetail } from "@/types/wiki";

// Successful fresh reads remain reusable. A failed read can retain a recent
// successful timestamp, but that data must not authorize another iframe.
export function fetchAuthorizedFile(
  queryClient: QueryClient,
  fileId: string,
): Promise<FileDetail | null> {
  const options = fileQueryOptions(fileId);
  const failed = queryClient.getQueryState(options.queryKey)?.status === "error";
  return queryClient
    .query(failed ? { ...options, staleTime: 0 } : options)
    .catch((error: unknown) => {
      if (!(error instanceof CancelledError) && !isAbortError(error))
        console.warn("Document metadata authorization failed");
      return null;
    });
}
