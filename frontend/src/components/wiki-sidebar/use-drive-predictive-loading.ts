"use client";

import { useQueryClient } from "@tanstack/react-query";
import { type PointerEvent as ReactPointerEvent, useEffect, useRef } from "react";

import { driveTreeQueryOptions, folderFilesQueryOptions } from "@/hooks/use-wiki-tree";
import { handlePrefetchError } from "@/lib/handle-prefetch-error";
import { allowsReusableSpeculation, readNavigatorHints } from "@/lib/speculation-policy";

export const DRIVE_PREFETCH_DELAY_MS = 150;

interface PreparationOwner {
  pending: boolean;
  strongIntent: boolean;
}

export function useDrivePredictiveLoading(driveId: string, expanded: boolean) {
  const queryClient = useQueryClient();
  const focusedRef = useRef(false);
  const hoveredRef = useRef(false);
  const ownerRef = useRef<null | PreparationOwner>(null);
  const timerRef = useRef<null | number>(null);

  const cancelTimer = () => {
    if (timerRef.current === null) return;
    window.clearTimeout(timerRef.current);
    timerRef.current = null;
  };

  const prepare = async (strongIntent = false) => {
    const owner = ownerRef.current;
    if (expanded || owner === null || (!strongIntent && !ownsSpeculation())) return;
    owner.strongIntent ||= strongIntent;
    if (owner.pending) return;
    owner.pending = true;
    try {
      const treeOptions = driveTreeQueryOptions(driveId, true);
      await queryClient.query(treeOptions).catch(handlePrefetchError);
      const tree = queryClient.getQueryData(treeOptions.queryKey);
      if (
        ownerRef.current === owner &&
        (owner.strongIntent || ownsSpeculation()) &&
        tree &&
        !tree.filesComplete
      ) {
        await queryClient.query(folderFilesQueryOptions(driveId)).catch(handlePrefetchError);
      }
    } finally {
      owner.pending = false;
      owner.strongIntent = false;
    }
  };

  const ownsSpeculation = () =>
    (focusedRef.current || hoveredRef.current) && allowsDriveSpeculation();

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
    const owner: PreparationOwner = { pending: false, strongIntent: false };
    ownerRef.current = owner;
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    return () => {
      if (ownerRef.current === owner) ownerRef.current = null;
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current);
        timerRef.current = null;
      }
    };
  }, [driveId, expanded, queryClient]);

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
      void prepare(true);
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
