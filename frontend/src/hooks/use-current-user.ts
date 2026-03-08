"use client";

import { useQuery } from "@tanstack/react-query";
import { User } from "@/types";
import { fetchCurrentUser } from "@/lib/auth";

export function useCurrentUser() {
  return useQuery<User | null>({
    queryKey: ["currentUser"],
    queryFn: fetchCurrentUser,
  });
}
