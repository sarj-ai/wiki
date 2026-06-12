"use client";

import { useQuery } from "@tanstack/react-query";

import { type User } from "@/lib/auth";
import { wikiApi } from "@/lib/wiki-api";
import { authKeys } from "@/lib/wiki-keys";

/**
 * Current user, hydrated from the (app) layout's server prefetch.
 * The queryFn throws so unrecoverable 401s hit query-provider's
 * /login redirect; focus refetch doubles as the expired-session canary.
 */
export function useCurrentUser() {
  return useQuery<User>({
    queryKey: authKeys.me,
    queryFn: wikiApi.getMe,
    staleTime: 5 * 60 * 1000,
  });
}
