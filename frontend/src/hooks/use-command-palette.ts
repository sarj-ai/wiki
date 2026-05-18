"use client";

import { useHotkeys } from "react-hotkeys-hook";
import { create } from "zustand";

/**
 * Open/closed state for the command palette. Backed by zustand so any
 * component can read or toggle it without prop-drilling — the sidebar
 * search button, the global Cmd+K listener, and the dialog itself all
 * subscribe to one store. Not persisted: a reload should never reopen
 * the palette.
 */
interface CommandPaletteState {
  open: boolean;
  setOpen: (next: boolean) => void;
  toggle: () => void;
}

const useCommandPaletteStore = create<CommandPaletteState>((set, get) => ({
  open: false,
  setOpen: (next) => {
    if (get().open === next) return;
    set({ open: next });
  },
  toggle: () => {
    set({ open: !get().open });
  },
}));

export function useCommandPaletteState(): {
  open: boolean;
  setOpen: (next: boolean) => void;
  toggle: () => void;
} {
  const open = useCommandPaletteStore((s) => s.open);
  const setOpen = useCommandPaletteStore((s) => s.setOpen);
  const toggle = useCommandPaletteStore((s) => s.toggle);
  return { open, setOpen, toggle };
}

/**
 * Binds Cmd+K (macOS) / Ctrl+K (other) to toggle the palette. Mount
 * once near the root — typically from the palette component itself
 * once it's rendered into the app layout.
 */
export function useCommandPaletteHotkey(): void {
  useHotkeys(
    "mod+k",
    () => {
      useCommandPaletteStore.getState().toggle();
    },
    {
      preventDefault: true,
    },
  );
}
