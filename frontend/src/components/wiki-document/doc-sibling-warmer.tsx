"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";

import { useDocViewerStore } from "@/components/wiki-document/doc-viewer-store";
import { computeWarmSiblings } from "@/lib/doc-siblings";

export function DocSiblingWarmer() {
  const queryClient = useQueryClient();
  const setWarm = useDocViewerStore((s) => s.setWarm);

  useEffect(() => {
    let handle: number | null = null;

    const warm = (fileId: string | null) => {
      if (handle !== null) cancelIdle(handle);
      if (!fileId) {
        setWarm([]);
        return;
      }
      handle = scheduleIdle(() => {
        const targets = computeWarmSiblings(queryClient, fileId);
        setWarm(targets.map((target) => ({ ...target, reloadKey: 0 })));
      });
    };

    warm(useDocViewerStore.getState().active?.fileId ?? null);
    const unsubscribe = useDocViewerStore.subscribe((state, prev) => {
      if (state.active?.fileId !== prev.active?.fileId) warm(state.active?.fileId ?? null);
    });

    return () => {
      unsubscribe();
      if (handle !== null) cancelIdle(handle);
    };
  }, [queryClient, setWarm]);

  return null;
}

function scheduleIdle(callback: () => void): number {
  if (typeof window.requestIdleCallback === "function") {
    return window.requestIdleCallback(callback, { timeout: 2000 });
  }
  return window.setTimeout(callback, 200);
}

function cancelIdle(handle: number): void {
  if (typeof window.cancelIdleCallback === "function") window.cancelIdleCallback(handle);
  else window.clearTimeout(handle);
}
