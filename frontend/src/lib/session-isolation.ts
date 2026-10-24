"use client";

import type { QueryClient } from "@tanstack/react-query";

import { useDocViewerStore } from "@/components/wiki-document/doc-viewer-store";
import { useDragOverStore } from "@/components/wiki-sidebar/use-drag-over-store";
import { useInFlightMovesStore } from "@/components/wiki-sidebar/use-in-flight-moves";
import { resetExpansionState } from "@/hooks/use-expanded-folders";
import { clearUserBrowserStorage } from "@/lib/browser-storage";
import { retireMutationSession } from "@/lib/mutation-session";

const SESSION_CLEAR_CHANNEL = "sarj-wiki:session-clear";
let sharedSessionChannel: BroadcastChannel | null = null;

export function clearAuthenticatedSession(queryClient: QueryClient, userId: null | string): void {
  retireMutationSession(queryClient);
  resetSidebarState();
  resetExpansionState(userId);
  if (userId) {
    clearUserBrowserStorage(userId);
  }
  void queryClient.cancelQueries();
  queryClient.clear();
  resetDocumentViewer();
}

export function resetSidebarState(): void {
  useDragOverStore.getState().reset();
  useInFlightMovesStore.getState().reset();
}

export function resetDocumentViewer(): void {
  useDocViewerStore.getState().reset();
}

export function broadcastSessionClear(): void {
  const channel = sessionChannel();
  if (!channel) return;
  channel.postMessage("clear");
}

export function subscribeToSessionClear(onClear: () => void): () => void {
  const channel = sessionChannel();
  if (!channel) return () => null;
  const handleMessage = (event: MessageEvent<unknown>) => {
    if (event.data === "clear") onClear();
  };
  channel.addEventListener("message", handleMessage);
  return () => {
    channel.removeEventListener("message", handleMessage);
  };
}

function sessionChannel(): BroadcastChannel | null {
  if (typeof BroadcastChannel === "undefined") return null;
  sharedSessionChannel ??= new BroadcastChannel(SESSION_CLEAR_CHANNEL);
  return sharedSessionChannel;
}
