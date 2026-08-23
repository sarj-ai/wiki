"use client";

import { useQueryClient } from "@tanstack/react-query";
import { type PointerEvent as ReactPointerEvent, useEffect, useRef } from "react";

import { driveTreeQueryOptions, folderFilesQueryOptions } from "@/hooks/use-wiki-tree";
import { allowsReusableSpeculation, readNavigatorHints } from "@/lib/speculation-policy";

export const DRIVE_PREFETCH_DELAY_MS = 150;

export function useDrivePredictiveLoading(driveId: string, expanded: boolean) {
  const queryClient = useQueryClient();
  const focusedRef = useRef(false);
  const hoveredRef = useRef(false);
  const timerRef = useRef<null | number>(null);

  const cancelTimer = () => {
    if (timerRef.current === null) return;
    window.clearTimeout(timerRef.current);
    timerRef.current = null;
  };

  const prepare = async () => {
    if (expanded) return;
    const treeOptions = driveTreeQueryOptions(driveId, true);
    await queryClient.prefetchQuery(treeOptions);
    const tree = queryClient.getQueryData(treeOptions.queryKey);
    if (tree && !tree.filesComplete) {
      await queryClient.prefetchQuery(folderFilesQueryOptions(driveId));
    }
  };

  const schedule = () => {
    if (expanded || timerRef.current !== null || !allowsDriveSpeculation()) return;
    timerRef.current = window.setTimeout(() => {
      timerRef.current = null;
      void prepare();
    }, DRIVE_PREFETCH_DELAY_MS);
  };

  const cancelIfIdle = () => {
    if (!focusedRef.current && !hoveredRef.current) cancelTimer();
  };

  useEffect(() => {
    if (expanded && timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    return () => {
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    };
  }, [driveId, expanded]);

  return {
    onBlur: () => {
      focusedRef.current = false;
      cancelIfIdle();
    },
    onFocus: () => {
      focusedRef.current = true;
      schedule();
    },
    onPointerDown: (event: ReactPointerEvent<HTMLButtonElement>) => {
      if (event.button !== 0 || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) {
        return;
      }
      cancelTimer();
      void prepare();
    },
    onPointerEnter: (event: ReactPointerEvent<HTMLButtonElement>) => {
      if (event.pointerType !== "mouse" && event.pointerType !== "pen") return;
      hoveredRef.current = true;
      schedule();
    },
    onPointerLeave: (event: ReactPointerEvent<HTMLButtonElement>) => {
      if (event.pointerType !== "mouse" && event.pointerType !== "pen") return;
      hoveredRef.current = false;
      cancelIfIdle();
    },
  };
}

function allowsDriveSpeculation(): boolean {
  if (document.visibilityState !== "visible") return false;
  const hints = readNavigatorHints(navigator);
  return allowsReusableSpeculation(hints) && hints.connection?.effectiveType !== "slow-2g";
}
