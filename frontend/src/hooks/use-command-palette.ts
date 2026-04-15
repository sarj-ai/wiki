"use client";

import { useCallback, useEffect, useSyncExternalStore } from "react";

/**
 * Module-level singleton for the command palette's open/closed state.
 * Any component can read the current state or toggle it — the trigger
 * button in the sidebar header, the global Cmd+K keydown listener, and
 * the palette dialog itself all share one store without prop drilling.
 */
let isOpen = false;
const subscribers = new Set<() => void>();

function notify() {
  subscribers.forEach((fn) => fn());
}

function subscribe(listener: () => void) {
  subscribers.add(listener);
  return () => subscribers.delete(listener);
}

function getSnapshot() {
  return isOpen;
}

function getServerSnapshot() {
  return false;
}

function setOpen(next: boolean) {
  if (isOpen === next) return;
  isOpen = next;
  notify();
}

export function useCommandPaletteState(): {
  open: boolean;
  setOpen: (next: boolean) => void;
  toggle: () => void;
} {
  const open = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
  const toggle = useCallback(() => setOpen(!isOpen), []);
  return { open, setOpen, toggle };
}

/**
 * Binds Cmd+K (macOS) / Ctrl+K (other) to toggle the palette. Meant
 * to be mounted once near the root — typically from the palette
 * component itself when it's rendered into the app layout.
 *
 * Also swallows the browser's default for the key combo so we don't
 * trigger Chrome's address-bar focus on the same shortcut.
 */
export function useCommandPaletteHotkey(): void {
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      const isShortcut = (event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k";
      if (!isShortcut) return;
      event.preventDefault();
      setOpen(!isOpen);
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);
}
