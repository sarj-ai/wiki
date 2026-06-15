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
  warm: ActiveDoc[];
  slot: HTMLElement | null;
  setActive: (active: ActiveDoc | null) => void;
  setPrewarm: (prewarm: ActiveDoc | null) => void;
  setWarm: (warm: ActiveDoc[]) => void;
  setSlot: (slot: HTMLElement | null) => void;
}

export const useDocViewerStore = create<DocViewerState>((set) => ({
  active: null,
  prewarm: null,
  warm: [],
  slot: null,
  setActive: (active) => {
    set({ active });
  },
  setPrewarm: (prewarm) => {
    set((state) => (state.prewarm?.fileId === prewarm?.fileId ? state : { ...state, prewarm }));
  },
  setWarm: (warm) => {
    set({ warm });
  },
  setSlot: (slot) => {
    set({ slot });
  },
}));
