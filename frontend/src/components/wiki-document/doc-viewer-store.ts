"use client";

import { create } from "zustand";

export interface ActiveDoc {
  fileId: string;
  mimeType: string;
  reloadKey: number;
  title: string;
  version?: string;
}

interface DocViewerState {
  active: ActiveDoc | null;
  activeOwner: null | symbol;
  clearActive: (owner: symbol) => void;
  pool: ActiveDoc[];
  prewarm: ActiveDoc | null;
  reset: () => void;
  setActive: (active: ActiveDoc, owner: symbol) => void;
  setPrewarm: (prewarm: ActiveDoc | null) => void;
  setSlot: (slot: HTMLElement | null, owner: symbol) => void;
  setWarm: (warm: ActiveDoc[]) => void;
  slot: HTMLElement | null;
  slotOwner: null | symbol;
  warm: ActiveDoc[];
}

const MAX_POOL = 3;

export const useDocViewerStore = create<DocViewerState>((set) => ({
  active: null,
  activeOwner: null,
  clearActive: (owner) => {
    set((state) => (state.activeOwner === owner ? { active: null, activeOwner: null } : state));
  },
  pool: [],
  prewarm: null,
  reset: () => {
    set({
      active: null,
      activeOwner: null,
      pool: [],
      prewarm: null,
      slot: null,
      slotOwner: null,
      warm: [],
    });
  },
  setActive: (active, owner) => {
    set((state) => ({
      active,
      activeOwner: owner,
      pool: reconcilePool(state.pool, active),
      prewarm: state.prewarm?.fileId === active.fileId ? null : state.prewarm,
    }));
  },
  setPrewarm: (prewarm) => {
    set((state) => {
      // A shared authorization may finish after navigation already consumed
      // this target. Keep any different pending target rather than reviving it.
      if (prewarm !== null && state.active?.fileId === prewarm.fileId) return state;
      return state.prewarm?.fileId === prewarm?.fileId ? state : { ...state, prewarm };
    });
  },
  setSlot: (slot, owner) => {
    if (slot !== null) {
      set({ slot, slotOwner: owner });
      return;
    }
    set((state) => (state.slotOwner === owner ? { slot: null, slotOwner: null } : state));
  },
  setWarm: (warm) => {
    set({ warm });
  },
  slot: null,
  slotOwner: null,
  warm: [],
}));

function reconcilePool(pool: ActiveDoc[], active: ActiveDoc): ActiveDoc[] {
  const head = pool[0];
  if (
    head?.fileId === active.fileId &&
    head.reloadKey === active.reloadKey &&
    head.mimeType === active.mimeType &&
    head.title === active.title &&
    head.version === active.version
  ) {
    return pool;
  }
  return [active, ...pool.filter((entry) => entry.fileId !== active.fileId)].slice(0, MAX_POOL);
}
