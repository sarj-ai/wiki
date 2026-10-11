"use client";

import { queryOptions, useQuery } from "@tanstack/react-query";

import type { User } from "@/lib/auth";
import { authKeys } from "@/lib/wiki-keys";
import { WIKI_READ_API } from "@/lib/wiki-read-api";

export function currentUserQueryOptions() {
  return queryOptions<User>({
    queryFn: async ({ signal }) => await WIKI_READ_API.getMe(signal),
    queryKey: authKeys.me,
    staleTime: 5 * 60 * 1000,
  });
}

export function useCurrentUser() {
  return useQuery(currentUserQueryOptions());
}
