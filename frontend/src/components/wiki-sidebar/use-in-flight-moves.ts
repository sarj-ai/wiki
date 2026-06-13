"use client";

import { create } from "zustand";

interface InFlightMovesState {
  ids: Set<string>;
  start: (fileId: string) => void;
  end: (fileId: string) => void;
}

export const useInFlightMovesStore = create<InFlightMovesState>((set) => ({
  ids: new Set(),
  start: (fileId) => {
    set((state) => {
      if (state.ids.has(fileId)) return state;
      const ids = new Set(state.ids);
      ids.add(fileId);
      return { ids };
    });
  },
  end: (fileId) => {
    set((state) => {
      if (!state.ids.has(fileId)) return state;
      const ids = new Set(state.ids);
      ids.delete(fileId);
      return { ids };
    });
  },
}));

export function useIsMovingFile(fileId: string): boolean {
  return useInFlightMovesStore((s) => s.ids.has(fileId));
}
