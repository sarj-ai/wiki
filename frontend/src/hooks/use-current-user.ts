"use client";

import { useQuery } from "@tanstack/react-query";

import type { User } from "@/lib/auth";
import { authKeys } from "@/lib/wiki-keys";
import { WIKI_READ_API } from "@/lib/wiki-read-api";

export function useCurrentUser() {
  return useQuery<User>({
    queryFn: WIKI_READ_API.getMe,
    queryKey: authKeys.me,
    staleTime: 5 * 60 * 1000,
  });
}
