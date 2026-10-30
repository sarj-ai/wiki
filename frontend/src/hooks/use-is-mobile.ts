"use client";

import { useSyncExternalStore } from "react";

const MOBILE_BREAKPOINT = 768;

export function useIsMobile(): boolean {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}

function subscribe(callback: () => void): () => void {
  const mql = window.matchMedia(`(max-width: ${MOBILE_BREAKPOINT - 1}px)`);
  mql.addEventListener("change", callback);
  return () => {
    mql.removeEventListener("change", callback);
  };
}

function getSnapshot(): boolean {
  return window.innerWidth < MOBILE_BREAKPOINT;
}

function getServerSnapshot(): boolean {
  return false;
}
