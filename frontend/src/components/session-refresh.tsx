"use client";

import { useEffect } from "react";

import { deduplicatedRefresh } from "@/lib/session-client";
import { notifyTerminalUnauthorized } from "@/lib/session-events";
import { LastRefreshMsSchema } from "@/lib/storage-schemas";

const REFRESH_INTERVAL_MS = 20 * 60 * 1000;

const CHECK_INTERVAL_MS = 60 * 1000;

const LAST_REFRESH_KEY = "sarj-wiki:last-session-refresh";

export function SessionRefresh() {
  useEffect(() => {
    const controller = new AbortController();
    let lastRefreshMs = 0;
    let ownsUnstoredRefresh = false;
    let failedMarker: string | undefined;
    let pendingMarker: string | undefined;
    const releaseMarker = (marker: string | undefined) => {
      if (marker === undefined || pendingMarker !== marker) return;
      pendingMarker = undefined;
      lastRefreshMs = 0;
      ownsUnstoredRefresh = false;
      failedMarker = marker;
      try {
        if (localStorage.getItem(LAST_REFRESH_KEY) === marker) {
          try {
            localStorage.removeItem(LAST_REFRESH_KEY);
          } catch {
            // Some storage policies deny deletion but still allow writes. An
            // expired owned marker keeps remounts from suppressing failed work.
            if (localStorage.getItem(LAST_REFRESH_KEY) === marker)
              localStorage.setItem(LAST_REFRESH_KEY, "0");
          }
        }
        failedMarker = undefined;
      } catch {
        // Storage failure must not prevent cancellation and timer cleanup.
      }
    };
    const maybeRefresh = () => {
      if (document.visibilityState !== "visible") return;
      try {
        const stored = localStorage.getItem(LAST_REFRESH_KEY);
        if (stored !== failedMarker) failedMarker = undefined;
        lastRefreshMs = Math.max(
          ownsUnstoredRefresh ? lastRefreshMs : 0,
          stored === failedMarker ? 0 : LastRefreshMsSchema.parse(stored),
        );
      } catch {
        // Keep the normal interval within this mount when storage is denied.
      }
      const now = Date.now();
      if (now - lastRefreshMs < REFRESH_INTERVAL_MS) return;
      const marker = String(now);
      lastRefreshMs = now;
      pendingMarker = marker;
      try {
        localStorage.setItem(LAST_REFRESH_KEY, marker);
        ownsUnstoredRefresh = false;
      } catch {
        ownsUnstoredRefresh = true;
        // Background refresh still works without cross-tab coordination.
      }
      void (async () => {
        const result = await deduplicatedRefresh(controller.signal);
        if (controller.signal.aborted) return;
        if (result === "refreshed") pendingMarker = undefined;
        else releaseMarker(marker);
        if (result === "unauthorized") notifyTerminalUnauthorized();
      })().catch(() => {
        // Unmount only releases this waiter; the shared refresh remains usable
        // by foreground reads and the replacement session owner.
        if (!controller.signal.aborted) releaseMarker(marker);
      });
    };
    let intervalId: number | undefined;
    const syncVisibility = () => {
      if (document.visibilityState !== "visible") {
        if (intervalId !== undefined) window.clearInterval(intervalId);
        intervalId = undefined;
        return;
      }
      maybeRefresh();
      intervalId ??= window.setInterval(maybeRefresh, CHECK_INTERVAL_MS);
    };
    syncVisibility();
    document.addEventListener("visibilitychange", syncVisibility);
    return () => {
      releaseMarker(pendingMarker);
      controller.abort();
      if (intervalId !== undefined) window.clearInterval(intervalId);
      document.removeEventListener("visibilitychange", syncVisibility);
    };
  }, []);
  return null;
}
