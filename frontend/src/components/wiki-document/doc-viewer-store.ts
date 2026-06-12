"use client";

import { create } from "zustand";

export interface ActiveDoc {
  fileId: string;
  mimeType: string;
  title: string;
  reloadKey: number;
}

interface DocViewerState {
  active: ActiveDoc | null;
  slot: HTMLElement | null;
  setActive: (active: ActiveDoc | null) => void;
  setSlot: (slot: HTMLElement | null) => void;
}

export const useDocViewerStore = create<DocViewerState>((set) => ({
  active: null,
  slot: null,
  setActive: (active) => {
    set({ active });
  },
  setSlot: (slot) => {
    set({ slot });
  },
}));
