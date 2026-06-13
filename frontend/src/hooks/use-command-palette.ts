"use client";

import { useHotkeys } from "react-hotkeys-hook";
import { create } from "zustand";

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
