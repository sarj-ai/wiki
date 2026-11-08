"use client";

import { useSyncExternalStore } from "react";

const subscribe = () => () => undefined;
const getShortcut = () =>
  /Mac|iPhone|iPad|iPod/u.test(navigator.userAgent) ? "⌘K" : "Ctrl K";
const getServerShortcut = () => "Ctrl K";

export function useSearchShortcut(): string {
  return useSyncExternalStore(subscribe, getShortcut, getServerShortcut);
}
