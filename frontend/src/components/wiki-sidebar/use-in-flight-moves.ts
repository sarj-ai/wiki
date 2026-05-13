"use client";

import { create } from "zustand";

/**
 * Tracks the set of file ids whose move mutation is currently in
 * flight. Subscribers (FileRow / TreeNode header) render a spinner
 * overlay so the user gets immediate feedback that the drop landed
 * AND that the move is still resolving with Drive — without that,
 * the row appears at the destination instantly via the optimistic
 * cache update and there's no signal that anything is still happening.
 *
 * Lives in zustand for the same reason as ``use-drag-over-store``:
 * selector hooks let only the affected row re-render when the set
 * mutates, so a 1000-row sidebar doesn't redraw on every move.
 */

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

/**
 * Selector: true while the row's move is settling with Drive.
 */
export function useIsMovingFile(fileId: string): boolean {
  return useInFlightMovesStore((s) => s.ids.has(fileId));
}
