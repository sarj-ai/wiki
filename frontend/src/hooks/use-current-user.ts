"use client";

import { useQuery } from "@tanstack/react-query";

import type { User } from "@/lib/auth";
import { wikiApi } from "@/lib/wiki-api";
import { authKeys } from "@/lib/wiki-keys";

export function useCurrentUser() {
  return useQuery<User>({
    queryFn: wikiApi.getMe,
    queryKey: authKeys.me,
    staleTime: 5 * 60 * 1000,
  });
}
