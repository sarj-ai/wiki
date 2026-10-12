"use client";

import { useHotkeys } from "react-hotkeys-hook";
import { create } from "zustand";

import type { SidebarNavigationIntent } from "@/components/wiki-sidebar/use-sidebar-navigation";

interface CommandPaletteState {
  mounted: boolean;
  navigationOwner: null | SidebarNavigationIntent;
  onDocumentNavigate: ((href: string) => void) | null;
  open: boolean;
  returnFocusRef: React.RefObject<HTMLElement | null>;
  setOpen: (
    next: boolean,
    opener?: HTMLElement,
    navigation?: null | SidebarNavigationIntent,
  ) => void;
  toggle: () => void;
}

const useCommandPaletteStore = create<CommandPaletteState>((set, get) => {
  let generation = 0;
  let releaseNavigation: (() => void) | null = null;
  return {
    mounted: false,
    navigationOwner: null,
    onDocumentNavigate: null,
    open: false,
    returnFocusRef: { current: null },
    setOpen: (next, opener, navigation) => {
      if (get().open === next) return;
      const opening = ++generation;
      releaseNavigation?.();
      releaseNavigation = null;
      let onDocumentNavigate: ((href: string) => void) | null = null;
      if (next) {
        const candidate = opener ?? document.activeElement;
        get().returnFocusRef.current = candidate instanceof HTMLElement ? candidate : null;
        if (navigation && !navigation.signal.aborted) {
          const retire = () => {
            if (generation !== opening) return;
            releaseNavigation?.();
            releaseNavigation = null;
            set({ onDocumentNavigate: null });
          };
          navigation.signal.addEventListener("abort", retire, { once: true });
          releaseNavigation = () => navigation.signal.removeEventListener("abort", retire);
          onDocumentNavigate = (href) => {
            if (generation === opening && get().open && !navigation.signal.aborted)
              navigation.onNavigate(href);
          };
        }
      }
      set({ mounted: get().mounted || next, onDocumentNavigate, open: next });
    },
    toggle: () => {
      const next = !get().open;
      get().setOpen(next, undefined, next ? get().navigationOwner : null);
    },
  };
});

export function useCommandPaletteMounted(): boolean {
  return useCommandPaletteStore((s) => s.mounted);
}

export function useCommandPaletteState(): Omit<CommandPaletteState, "mounted" | "navigationOwner"> {
  const open = useCommandPaletteStore((s) => s.open);
  const setOpen = useCommandPaletteStore((s) => s.setOpen);
  const toggle = useCommandPaletteStore((s) => s.toggle);
  const returnFocusRef = useCommandPaletteStore((s) => s.returnFocusRef);
  const onDocumentNavigate = useCommandPaletteStore((s) => s.onDocumentNavigate);
  return { onDocumentNavigate, open, returnFocusRef, setOpen, toggle };
}

export function registerCommandPaletteNavigationOwner(owner: SidebarNavigationIntent): () => void {
  const retire = () => {
    owner.signal.removeEventListener("abort", retire);
    if (useCommandPaletteStore.getState().navigationOwner === owner)
      useCommandPaletteStore.setState({ navigationOwner: null });
  };
  if (owner.signal.aborted) return retire;
  useCommandPaletteStore.setState({ navigationOwner: owner });
  owner.signal.addEventListener("abort", retire, { once: true });
  return retire;
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
