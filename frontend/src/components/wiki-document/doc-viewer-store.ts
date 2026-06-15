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
  prewarm: ActiveDoc | null;
  slot: HTMLElement | null;
  setActive: (active: ActiveDoc | null) => void;
  setPrewarm: (prewarm: ActiveDoc | null) => void;
  setSlot: (slot: HTMLElement | null) => void;
}

export const useDocViewerStore = create<DocViewerState>((set) => ({
  active: null,
  prewarm: null,
  slot: null,
  setActive: (active) => {
    set({ active });
  },
  setPrewarm: (prewarm) => {
    set((state) => (state.prewarm?.fileId === prewarm?.fileId ? state : { ...state, prewarm }));
  },
  setSlot: (slot) => {
    set({ slot });
  },
}));
