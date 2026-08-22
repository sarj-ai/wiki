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
  clearViewerLoad: (identity: string, owner: symbol) => void;
  markViewerLoading: (identity: string, owner: symbol) => void;
  markViewerReady: (identity: string, owner: symbol) => void;
  pool: ActiveDoc[];
  prewarm: ActiveDoc | null;
  reset: () => void;
  setActive: (active: ActiveDoc, owner: symbol) => void;
  setPrewarm: (prewarm: ActiveDoc | null) => void;
  setSlot: (slot: HTMLElement | null, owner: symbol) => void;
  setWarm: (warm: ActiveDoc[]) => void;
  slot: HTMLElement | null;
  slotOwner: null | symbol;
  viewerLoads: Record<string, ViewerLoad>;
  warm: ActiveDoc[];
}

interface ViewerLoad {
  owner: symbol;
  status: "loading" | "ready";
}

const MAX_POOL = 3;

export const useDocViewerStore = create<DocViewerState>((set) => ({
  active: null,
  activeOwner: null,
  clearActive: (owner) => {
    set((state) => (state.activeOwner === owner ? { active: null, activeOwner: null } : state));
  },
  clearViewerLoad: (identity, owner) => {
    set((state) => {
      if (state.viewerLoads[identity]?.owner !== owner) return state;
      const { [identity]: _removed, ...viewerLoads } = state.viewerLoads;
      return { viewerLoads };
    });
  },
  markViewerLoading: (identity, owner) => {
    set((state) => ({
      viewerLoads: { ...state.viewerLoads, [identity]: { owner, status: "loading" } },
    }));
  },
  markViewerReady: (identity, owner) => {
    set((state) =>
      state.viewerLoads[identity]?.owner === owner
        ? {
            viewerLoads: { ...state.viewerLoads, [identity]: { owner, status: "ready" } },
          }
        : state,
    );
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
      viewerLoads: {},
      warm: [],
    });
  },
  setActive: (active, owner) => {
    set((state) => ({ active, activeOwner: owner, pool: reconcilePool(state.pool, active) }));
  },
  setPrewarm: (prewarm) => {
    set((state) => (state.prewarm?.fileId === prewarm?.fileId ? state : { ...state, prewarm }));
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
  viewerLoads: {},
  warm: [],
}));

export function viewerIdentity(entry: ActiveDoc): string {
  const contentVersion = entry.mimeType === "text/html" ? (entry.version ?? "current") : "stable";
  return `${entry.fileId}:${entry.reloadKey}:${contentVersion}`;
}

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
