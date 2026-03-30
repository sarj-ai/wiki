"use client";

import { useCallback, useMemo } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";

export function useStarredPages() {
  const queryClient = useQueryClient();

  const { data: starredIds = [], ...rest } = useQuery<string[]>({
    queryKey: ["starred-pages"],
    queryFn: () => api.getStarredPages(),
  });

  const starredSet = useMemo(() => new Set(starredIds), [starredIds]);

  const toggleStar = useCallback(
    async (pageId: string) => {
      const isStarred = starredSet.has(pageId);
      // Optimistic update
      queryClient.setQueryData<string[]>(["starred-pages"], (prev = []) =>
        isStarred ? prev.filter((id) => id !== pageId) : [...prev, pageId]
      );
      try {
        if (isStarred) {
          await api.unstarPage(pageId);
        } else {
          await api.starPage(pageId);
        }
      } catch {
        // Revert on failure
        queryClient.invalidateQueries({ queryKey: ["starred-pages"] });
      }
    },
    [starredSet, queryClient]
  );

  return { starredIds: starredSet, toggleStar, ...rest };
}
