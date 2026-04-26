"use client";

import { useCallback, useSyncExternalStore } from "react";
import { useHotkeys } from "react-hotkeys-hook";

/**
 * Module-level singleton for the command palette's open/closed state.
 * Any component can read the current state or toggle it — the trigger
 * button in the sidebar header, the global Cmd+K keydown listener, and
 * the palette dialog itself all share one store without prop drilling.
 */
let isOpen = false;
const subscribers = new Set<() => void>();

function notify() {
  subscribers.forEach((fn) => { fn(); });
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
  const toggle = useCallback(() => { setOpen(!isOpen); }, []);
  return { open, setOpen, toggle };
}

/**
 * Binds Cmd+K (macOS) / Ctrl+K (other) to toggle the palette. Meant
 * to be mounted once near the root — typically from the palette
 * component itself when it's rendered into the app layout.
 */
export function useCommandPaletteHotkey(): void {
  useHotkeys("mod+k", () => { setOpen(!isOpen); }, { preventDefault: true });
}
