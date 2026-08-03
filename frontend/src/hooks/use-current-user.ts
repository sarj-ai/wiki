"use client";

import { useQuery } from "@tanstack/react-query";

import type { User } from "@/lib/auth";
import { authKeys } from "@/lib/wiki-keys";
import { wikiReadApi } from "@/lib/wiki-read-client";

export function useCurrentUser() {
  return useQuery<User>({
    queryFn: wikiReadApi.getMe,
    queryKey: authKeys.me,
    staleTime: 5 * 60 * 1000,
  });
}
