"use client";

import { useEffect, useCallback, useRef } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { Page } from "@/types";

// Minimum 5 min between Drive syncs (shared across all hook instances)
const SYNC_COOLDOWN_MS = 300_000;
let lastSyncTime = 0;

export function usePageTree(workspaceId?: string) {
  const queryClient = useQueryClient();

  const query = useQuery<Page[]>({
    queryKey: ["pages", "tree", workspaceId],
    queryFn: async () => {
      const tree = await api.getPageTree();
      if (workspaceId) {
        return tree.filter((p) => p.workspaceId === workspaceId);
      }
      return tree;
    },
  });

  const sync = useCallback(() => {
    const now = Date.now();
    if (now - lastSyncTime < SYNC_COOLDOWN_MS) return;
    lastSyncTime = now;

    api.syncPages().then((result) => {
      if (result && (result.removed > 0 || result.added > 0 || result.updated > 0)) {
        queryClient.invalidateQueries({ queryKey: ["pages"] });
      }
    }).catch(() => {});
  }, [queryClient]);

  // Sync once on mount + every time the tab regains focus (with cooldown)
  useEffect(() => {
    sync();
    window.addEventListener("focus", sync);
    return () => window.removeEventListener("focus", sync);
  }, [sync]);

  return query;
}
