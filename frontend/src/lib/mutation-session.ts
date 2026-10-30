import type { QueryClient } from "@tanstack/react-query";

const OWNERS = new WeakMap<QueryClient, AbortController>();

export function captureMutationSession(queryClient: QueryClient): AbortSignal {
  let owner = OWNERS.get(queryClient);
  if (!owner) {
    owner = new AbortController();
    OWNERS.set(queryClient, owner);
  }
  return owner.signal;
}

export function activateMutationSession(queryClient: QueryClient): void {
  if (OWNERS.get(queryClient)?.signal.aborted) OWNERS.set(queryClient, new AbortController());
}

export function retireMutationSession(queryClient: QueryClient): void {
  captureMutationSession(queryClient);
  OWNERS.get(queryClient)?.abort(
    new DOMException("The mutation session was retired", "AbortError"),
  );
}
