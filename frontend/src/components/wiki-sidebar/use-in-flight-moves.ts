"use client";

import { create } from "zustand";

interface InFlightMovesState {
  end: (fileId: string, owner: symbol) => void;
  ids: Set<string>;
  reset: () => void;
  start: (fileId: string, session: AbortSignal) => null | symbol;
}

interface SessionMoves {
  files: Map<string, symbol>;
  release: () => void;
}

const MOVES = new Map<string, { owner: symbol; session: AbortSignal }>();
const SESSIONS = new Map<AbortSignal, SessionMoves>();

export const useInFlightMovesStore = create<InFlightMovesState>((set) => ({
  end: (fileId, owner) => {
    const lease = MOVES.get(fileId);
    if (lease?.owner !== owner) return;
    releaseMove(fileId, lease.session);
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
    for (const session of SESSIONS.values()) session.release();
    SESSIONS.clear();
    MOVES.clear();
    set((state) => (state.ids.size === 0 ? state : { ids: new Set<string>() }));
  },
  start: (fileId, session) => {
    if (session.aborted) return null;
    const previous = MOVES.get(fileId);
    // An admitted move owns this file until it settles. Keep later gestures
    // from overlapping writes with a rollback based on an obsolete location.
    if (previous?.session === session) return null;
    const owner = Symbol(fileId);
    if (previous) releaseMove(fileId, previous.session);
    let moves = SESSIONS.get(session);
    if (!moves) {
      const files = new Map<string, symbol>();
      const onAbort = () => {
        const removed = new Set<string>();
        for (const [id, activeOwner] of files) {
          if (MOVES.get(id)?.owner !== activeOwner) continue;
          MOVES.delete(id);
          removed.add(id);
        }
        SESSIONS.get(session)?.release();
        SESSIONS.delete(session);
        set((state) => {
          if (removed.size === 0) return state;
          const ids = new Set(state.ids);
          for (const id of removed) ids.delete(id);
          return { ids };
        });
      };
      session.addEventListener("abort", onAbort, { once: true });
      moves = { files, release: () => session.removeEventListener("abort", onAbort) };
      SESSIONS.set(session, moves);
    }
    moves.files.set(fileId, owner);
    MOVES.set(fileId, { owner, session });
    set((state) => {
      if (state.ids.has(fileId)) return state;
      const ids = new Set(state.ids);
      ids.add(fileId);
      return { ids };
    });
    return owner;
  },
}));

function releaseMove(fileId: string, session: AbortSignal): void {
  const moves = SESSIONS.get(session);
  moves?.files.delete(fileId);
  if (moves?.files.size === 0) {
    moves.release();
    SESSIONS.delete(session);
  }
}

export function useIsMovingFile(fileId: string): boolean {
  return useInFlightMovesStore((s) => s.ids.has(fileId));
}
