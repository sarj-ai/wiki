"use client";

import { create } from "zustand";

export interface ActiveDoc {
  fileId: string;
  mimeType: string;
  reloadKey: number;
  title: string;
}

interface DocViewerState {
  active: ActiveDoc | null;
  prewarm: ActiveDoc | null;
  setActive: (active: ActiveDoc | null) => void;
  setPrewarm: (prewarm: ActiveDoc | null) => void;
  setSlot: (slot: HTMLElement | null) => void;
  setWarm: (warm: ActiveDoc[]) => void;
  slot: HTMLElement | null;
  warm: ActiveDoc[];
}

export const useDocViewerStore = create<DocViewerState>((set) => ({
  active: null,
  prewarm: null,
  setActive: (active) => {
    set({ active });
  },
  setPrewarm: (prewarm) => {
    set((state) => (state.prewarm?.fileId === prewarm?.fileId ? state : { ...state, prewarm }));
  },
  setSlot: (slot) => {
    set({ slot });
  },
  setWarm: (warm) => {
    set({ warm });
  },
  slot: null,
  warm: [],
}));
