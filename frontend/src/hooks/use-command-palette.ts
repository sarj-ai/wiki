"use client";

import { useHotkeys } from "react-hotkeys-hook";
import { create } from "zustand";

interface CommandPaletteState {
  mounted: boolean;
  open: boolean;
  setOpen: (next: boolean) => void;
  toggle: () => void;
}

const useCommandPaletteStore = create<CommandPaletteState>((set, get) => ({
  mounted: false,
  open: false,
  setOpen: (next) => {
    if (get().open === next) return;
    set({ mounted: get().mounted || next, open: next });
  },
  toggle: () => {
    const next = !get().open;
    set({ mounted: get().mounted || next, open: next });
  },
}));

export function useCommandPaletteMounted(): boolean {
  return useCommandPaletteStore((s) => s.mounted);
}

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
