"use client";

import { useEffect } from "react";

import { deduplicatedRefresh } from "@/lib/api-client";
import { ZLastRefreshMs } from "@/lib/storage-schemas";

const REFRESH_INTERVAL_MS = 20 * 60 * 1000;

const CHECK_INTERVAL_MS = 60 * 1000;

const LAST_REFRESH_KEY = "sarj-wiki:last-session-refresh";

export function SessionRefresh() {
  useEffect(() => {
    const maybeRefresh = () => {
      if (document.visibilityState !== "visible") return;
      const last = ZLastRefreshMs.parse(localStorage.getItem(LAST_REFRESH_KEY));
      if (Date.now() - last < REFRESH_INTERVAL_MS) return;
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
