"use client";

import type { QueryClient } from "@tanstack/react-query";

import { useDocViewerStore } from "@/components/wiki-document/doc-viewer-store";
import { resetExpansionState } from "@/hooks/use-expanded-folders";
import { clearUserBrowserStorage } from "@/lib/browser-storage";

const SESSION_CLEAR_CHANNEL = "sarj-wiki:session-clear";
let sharedSessionChannel: BroadcastChannel | null = null;

export function clearAuthenticatedSession(queryClient: QueryClient, userId: null | string): void {
  resetExpansionState(userId);
  if (userId) {
    clearUserBrowserStorage(userId);
  }
  void queryClient.cancelQueries();
  queryClient.clear();
  resetDocumentViewer();
}

export function resetDocumentViewer(): void {
  useDocViewerStore.setState({ active: null, prewarm: null, slot: null, warm: [] });
}

export function broadcastSessionClear(): void {
  const channel = sessionChannel();
  if (!channel) return;
  // eslint-disable-next-line unicorn/require-post-message-target-origin -- BroadcastChannel.postMessage has no target-origin parameter.
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
