"use client";

import { create } from "zustand";

interface InFlightMovesState {
  end: (fileId: string) => void;
  ids: Set<string>;
  start: (fileId: string) => void;
}

export const useInFlightMovesStore = create<InFlightMovesState>((set) => ({
  end: (fileId) => {
    set((state) => {
      if (!state.ids.has(fileId)) return state;
      const ids = new Set(state.ids);
      ids.delete(fileId);
      return { ids };
    });
  },
  ids: new Set(),
  start: (fileId) => {
    set((state) => {
      if (state.ids.has(fileId)) return state;
      const ids = new Set(state.ids);
      ids.add(fileId);
      return { ids };
    });
  },
}));

export function useIsMovingFile(fileId: string): boolean {
  return useInFlightMovesStore((s) => s.ids.has(fileId));
}
