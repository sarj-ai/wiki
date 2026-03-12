"use client";

import { useQuery } from "@tanstack/react-query";
import type { Workspace } from "@/types";
import { api } from "@/lib/api";

export function useWorkspaces() {
  return useQuery<Workspace[]>({
    queryKey: ["workspaces"],
    queryFn: () => api.getWorkspaces(),
    refetchInterval: 60 * 1000, // Re-check every 60s
  });
}
