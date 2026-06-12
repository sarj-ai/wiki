"use client";

import { useEffect } from "react";

import { deduplicatedRefresh } from "@/lib/api-client";

// Rotate the auth cookies well inside the 30-minute access-token expiry
// (see backend _ACCESS_MAX_AGE) so in-flight queries never hit the
// 401 → refresh → replay path. Without this, every 30 minutes the first
// burst of sidebar queries all 401, refresh, and replay — measured as
// ~25% of folder-listing requests in the request logs.
const REFRESH_INTERVAL_MS = 20 * 60 * 1000;

// How often the loop re-checks whether a refresh is due. Cheap (a
// localStorage read), so it can be frequent enough to catch tabs
// returning from sleep close to token expiry.
const CHECK_INTERVAL_MS = 60 * 1000;

// Shared across tabs via localStorage so N open tabs coordinate to
// ~one refresh per window instead of N. Races are harmless — the
// backend's refresh tokens are not one-time-use (only logout bumps
// token_version), so a double refresh just wastes a request.
const LAST_REFRESH_KEY = "sarj-wiki:last-session-refresh";

export function SessionRefresh() {
  useEffect(() => {
    const maybeRefresh = () => {
      // Hidden tabs skip — their queries are idle, and the visible tab
      // (or this one, on its next visibilitychange) keeps the session
      // warm for everyone via the shared cookie.
      if (document.visibilityState !== "visible") return;
      const last = Number(localStorage.getItem(LAST_REFRESH_KEY) ?? "0");
      if (Date.now() - last < REFRESH_INTERVAL_MS) return;
      // Claim the slot before the round-trip so other tabs back off;
      // release it on failure so the next tick retries instead of
      // waiting out a full window on a transient error.
      localStorage.setItem(LAST_REFRESH_KEY, String(Date.now()));
      void deduplicatedRefresh().then((ok) => {
        if (!ok) localStorage.removeItem(LAST_REFRESH_KEY);
      });
    };
    maybeRefresh();
    const intervalId = window.setInterval(maybeRefresh, CHECK_INTERVAL_MS);
    document.addEventListener("visibilitychange", maybeRefresh);
    return () => {
      window.clearInterval(intervalId);
      document.removeEventListener("visibilitychange", maybeRefresh);
    };
  }, []);
  return null;
}
