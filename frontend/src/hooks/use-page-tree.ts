"use client";

import { useEffect, useCallback } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { Page } from "@/types";

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
    api.syncPages().then((result) => {
      if (result && (result.removed > 0 || result.added > 0 || result.updated > 0)) {
        queryClient.invalidateQueries({ queryKey: ["pages"] });
      }
    }).catch(() => {});
  }, [queryClient]);

  // Sync once on mount + every time the tab regains focus
  useEffect(() => {
    sync();

    function onFocus() {
      sync();
    }

    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [sync]);

  return query;
}
