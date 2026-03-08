"use client";

import { useQuery } from "@tanstack/react-query";
import type { Workspace } from "@/types";

export function useWorkspaces() {
  return useQuery<Workspace[]>({
    queryKey: ["workspaces"],
    queryFn: async () => {
      // Workspaces endpoint will be added in Phase 4 (Admin SDK sync).
      // For now, return empty — the sidebar handles this gracefully.
      return [];
    },
  });
}
