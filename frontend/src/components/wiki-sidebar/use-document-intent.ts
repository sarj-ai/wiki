"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useEffect, useRef } from "react";

import { useDocViewerStore } from "@/components/wiki-document/doc-viewer-store";
import { captureBrowserSession } from "@/lib/browser-session";
import { fetchAuthorizedFile } from "@/lib/fetch-authorized-file";
import {
  allowsIframeSpeculation,
  allowsReusableSpeculation,
  readNavigatorHints,
} from "@/lib/speculation-policy";

export const DOCUMENT_INTENT_PREFETCH_MS = 150;
export const DOCUMENT_INTENT_PREWARM_MS = 300;

interface DocumentIntentOptions {
  disabled: boolean;
  fileId: string;
  onStrongIntent?: () => void;
}

interface DocumentIntentHandlers {
  onBlur: () => void;
  onClick: (event: React.MouseEvent<HTMLAnchorElement>) => void;
  onFocus: () => void;
  onPointerDown: (event: React.PointerEvent<HTMLAnchorElement>) => void;
  onPointerEnter: (event: React.PointerEvent<HTMLAnchorElement>) => void;
  onPointerLeave: (event: React.PointerEvent<HTMLAnchorElement>) => void;
}

interface IntentState {
  committed: boolean;
  focused: boolean;
  generation: number;
  hovered: boolean;
  prefetchTimer: null | number;
  prewarmTimer: null | number;
}

export function useDocumentIntent({
  disabled,
  fileId,
  onStrongIntent,
}: DocumentIntentOptions): DocumentIntentHandlers {
  const queryClient = useQueryClient();
  const router = useRouter();
  const stateRef = useRef<IntentState>({
    committed: false,
    focused: false,
    generation: 0,
    hovered: false,
    prefetchTimer: null,
    prewarmTimer: null,
  });

  const prefetch = async () => {
    router.prefetch(`/d/${encodeURIComponent(fileId)}`);
    return await fetchAuthorizedFile(queryClient, fileId);
  };

  const prewarm = async (committed = false) => {
    const generation = stateRef.current.generation;
    const session = captureBrowserSession();
    const file = await prefetch();
    if (file === null || session.aborted) return;
    const state = stateRef.current;
    if (
      !committed &&
      (state.generation !== generation ||
        (!state.focused && !state.hovered) ||
        !allowsIframeIntentSpeculation())
    ) {
      return;
    }
    useDocViewerStore.getState().setPrewarm({
      fileId: file.id,
      mimeType: file.mimeType,
      reloadKey: 0,
      title: file.name,
      ...(!(file.modifiedTime === undefined) && { version: file.modifiedTime }),
    });
  };

  const startSpeculation = () => {
    if (disabled || stateRef.current.committed || !allowsSpeculation()) return;
    const state = stateRef.current;
    if (state.prefetchTimer !== null || state.prewarmTimer !== null) return;
    state.prefetchTimer = window.setTimeout(() => {
      state.prefetchTimer = null;
      if ((state.hovered || state.focused) && allowsSpeculation()) void prefetch();
    }, DOCUMENT_INTENT_PREFETCH_MS);
    state.prewarmTimer = window.setTimeout(() => {
      state.prewarmTimer = null;
      if ((state.hovered || state.focused) && allowsIframeIntentSpeculation()) void prewarm();
    }, DOCUMENT_INTENT_PREWARM_MS);
  };

  const endSpeculationIfIdle = () => {
    const state = stateRef.current;
    if (!state.hovered && !state.focused) {
      state.generation += 1;
      state.committed = false;
      clearIntentTimers(state);
    }
  };

  const commit = () => {
    if (disabled) return;
    stateRef.current.committed = true;
    clearIntentTimers(stateRef.current);
    onStrongIntent?.();
    void prewarm(true);
  };

  useEffect(() => {
    const state = stateRef.current;
    state.generation += 1;
    clearIntentTimers(state);
    state.committed = false;
    state.focused = false;
    state.hovered = false;
    return () => {
      state.generation += 1;
      clearIntentTimers(state);
    };
  }, [disabled, fileId, queryClient]);

  return {
    onBlur: () => {
      stateRef.current.focused = false;
      endSpeculationIfIdle();
    },
    onClick: (event) => {
      if (event.detail === 0) commit();
    },
    onFocus: () => {
      stateRef.current.focused = true;
      startSpeculation();
    },
    onPointerDown: (event) => {
      if (isPrimaryUnmodifiedPointer(event)) commit();
    },
    onPointerEnter: (event) => {
      if (!isHoverPointer(event.pointerType)) return;
      stateRef.current.hovered = true;
      startSpeculation();
    },
    onPointerLeave: (event) => {
      if (!isHoverPointer(event.pointerType)) return;
      stateRef.current.hovered = false;
      endSpeculationIfIdle();
    },
  };
}

function isHoverPointer(pointerType: string): boolean {
  return pointerType === "" || pointerType === "mouse" || pointerType === "pen";
}

function isPrimaryUnmodifiedPointer(event: React.PointerEvent<HTMLAnchorElement>): boolean {
  return event.button === 0 && !event.altKey && !event.ctrlKey && !event.metaKey && !event.shiftKey;
}

function allowsSpeculation(): boolean {
  if (document.visibilityState !== "visible") return false;
  return allowsReusableSpeculation(readNavigatorHints(navigator));
}

function allowsIframeIntentSpeculation(): boolean {
  if (document.visibilityState !== "visible") return false;
  return allowsIframeSpeculation(readNavigatorHints(navigator));
}

function clearIntentTimers(state: IntentState): void {
  if (state.prefetchTimer !== null) window.clearTimeout(state.prefetchTimer);
  if (state.prewarmTimer !== null) window.clearTimeout(state.prewarmTimer);
  state.prefetchTimer = null;
  state.prewarmTimer = null;
}
