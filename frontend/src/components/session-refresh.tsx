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
    let pendingMarker: string | undefined;
    const releaseMarker = (marker: string | undefined) => {
      if (marker === undefined || pendingMarker !== marker) return;
      pendingMarker = undefined;
      if (localStorage.getItem(LAST_REFRESH_KEY) === marker)
        localStorage.removeItem(LAST_REFRESH_KEY);
    };
    const maybeRefresh = () => {
      if (document.visibilityState !== "visible") return;
      const last = LastRefreshMsSchema.parse(localStorage.getItem(LAST_REFRESH_KEY));
      if (Date.now() - last < REFRESH_INTERVAL_MS) return;
      const marker = String(Date.now());
      pendingMarker = marker;
      localStorage.setItem(LAST_REFRESH_KEY, marker);
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
    maybeRefresh();
    const intervalId = window.setInterval(maybeRefresh, CHECK_INTERVAL_MS);
    document.addEventListener("visibilitychange", maybeRefresh);
    return () => {
      releaseMarker(pendingMarker);
      controller.abort();
      window.clearInterval(intervalId);
      document.removeEventListener("visibilitychange", maybeRefresh);
    };
  }, []);
  return null;
}
