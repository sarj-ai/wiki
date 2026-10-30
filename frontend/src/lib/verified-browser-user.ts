import type { QueryClient } from "@tanstack/react-query";

import { captureMutationSession } from "@/lib/mutation-session";

interface VerifiedIdentity {
  session: AbortSignal;
  userId: string;
}

const VERIFIED_IDENTITIES = new WeakMap<QueryClient, VerifiedIdentity>();

export function claimVerifiedBrowserUser(
  queryClient: QueryClient,
  userId: string,
  session: AbortSignal,
): boolean {
  if (session.aborted || session !== captureMutationSession(queryClient)) return false;
  const existing = readVerifiedBrowserUser(queryClient);
  if (existing !== null && existing !== userId) return false;
  VERIFIED_IDENTITIES.set(queryClient, { session, userId });
  return true;
}

export function readVerifiedBrowserUser(queryClient: QueryClient): null | string {
  const identity = VERIFIED_IDENTITIES.get(queryClient);
  return identity &&
    !identity.session.aborted &&
    identity.session === captureMutationSession(queryClient)
    ? identity.userId
    : null;
}
