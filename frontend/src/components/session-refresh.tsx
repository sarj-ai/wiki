"use client";

import { useEffect } from "react";

import { deduplicatedRefresh } from "@/lib/session-client";
import { notifyTerminalUnauthorized } from "@/lib/session-events";
import { ZLAST_REFRESH_MS } from "@/lib/storage-schemas";

const REFRESH_INTERVAL_MS = 20 * 60 * 1000;

const CHECK_INTERVAL_MS = 60 * 1000;

const LAST_REFRESH_KEY = "sarj-wiki:last-session-refresh";

export function SessionRefresh() {
  useEffect(() => {
    const maybeRefresh = () => {
      if (document.visibilityState !== "visible") return;
      const last = ZLAST_REFRESH_MS.parse(localStorage.getItem(LAST_REFRESH_KEY));
      if (Date.now() - last < REFRESH_INTERVAL_MS) return;
      localStorage.setItem(LAST_REFRESH_KEY, String(Date.now()));
      void deduplicatedRefresh().then((result) => {
        if (result !== "refreshed") localStorage.removeItem(LAST_REFRESH_KEY);
        if (result === "unauthorized") notifyTerminalUnauthorized();
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
