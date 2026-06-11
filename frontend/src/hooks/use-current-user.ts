"use client";

import { useQuery } from "@tanstack/react-query";

import { apiClient, validatedJson } from "@/lib/api-client";
import { type User } from "@/lib/auth";
import { zUserProfile } from "@/lib/generated/zod.gen";
import { authKeys } from "@/lib/wiki-keys";

/**
 * Current user, hydrated from the (app) layout's server prefetch.
 * The queryFn throws so unrecoverable 401s hit query-provider's
 * /login redirect; focus refetch doubles as the expired-session canary.
 */
export function useCurrentUser() {
  return useQuery<User>({
    queryKey: authKeys.me,
    queryFn: () => validatedJson(apiClient.get("auth/me"), zUserProfile),
    staleTime: 5 * 60 * 1000,
  });
}
