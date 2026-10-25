import { CancelledError } from "@tanstack/react-query";

import { isAbortError } from "@/lib/mutation-toast";

export function handlePrefetchError(error: unknown): void {
  if (!(error instanceof CancelledError) && !isAbortError(error))
    console.warn("Wiki prefetch could not finish");
}
