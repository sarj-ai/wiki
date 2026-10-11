"use client";

import { create } from "zustand";

interface InFlightMovesState {
  end: (fileId: string, owner: symbol) => void;
  ids: Set<string>;
  reset: () => void;
  start: (fileId: string, session: AbortSignal) => null | symbol;
}

const MOVES = new Map<string, { owner: symbol; release: () => void }>();

export const useInFlightMovesStore = create<InFlightMovesState>((set, get) => ({
  end: (fileId, owner) => {
    const lease = MOVES.get(fileId);
    if (lease?.owner !== owner) return;
    lease.release();
    MOVES.delete(fileId);
    set((state) => {
      if (!state.ids.has(fileId)) return state;
      const ids = new Set(state.ids);
      ids.delete(fileId);
      return { ids };
    });
  },
  ids: new Set(),
  reset: () => {
    for (const lease of MOVES.values()) lease.release();
    MOVES.clear();
    set((state) => (state.ids.size === 0 ? state : { ids: new Set<string>() }));
  },
  start: (fileId, session) => {
    if (session.aborted) return null;
    const owner = Symbol(fileId);
    MOVES.get(fileId)?.release();
    const onAbort = () => get().end(fileId, owner);
    session.addEventListener("abort", onAbort, { once: true });
    MOVES.set(fileId, {
      owner,
      release: () => session.removeEventListener("abort", onAbort),
    });
    set((state) => {
      if (state.ids.has(fileId)) return state;
      const ids = new Set(state.ids);
      ids.add(fileId);
      return { ids };
    });
    return owner;
  },
}));

export function useIsMovingFile(fileId: string): boolean {
  return useInFlightMovesStore((s) => s.ids.has(fileId));
}
