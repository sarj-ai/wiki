"use client";

import { useSyncExternalStore } from "react";

export function useReducedMotion(): boolean {
  return useSyncExternalStore(subscribe, snapshot, () => true);
}

let mediaQuery: MediaQueryList | null = null;
const LISTENERS = new Set<() => void>();

function currentQuery(): MediaQueryList {
  return (mediaQuery ??= window.matchMedia("(prefers-reduced-motion: reduce)"));
}

function subscribe(callback: () => void): () => void {
  const query = currentQuery();
  if (LISTENERS.size === 0) query.addEventListener("change", notify);
  LISTENERS.add(callback);
  return () => {
    LISTENERS.delete(callback);
    if (LISTENERS.size === 0) {
      query.removeEventListener("change", notify);
      mediaQuery = null;
    }
  };
}

function snapshot(): boolean {
  return currentQuery().matches;
}

function notify(): void {
  for (const listener of LISTENERS) listener();
}
