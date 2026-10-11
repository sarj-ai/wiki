"use client";

import { create } from "zustand";

export type DropIntent = "above" | "below" | "into";

interface DragOverState {
  activeContainerId: null | string;
  activeId: null | string;
  activeOwner: null | symbol;
  intent: DropIntent | null;
  overContainerId: null | string;
  overId: null | string;
  reset: (owner?: symbol) => void;
  setActive: (
    active: { id: null | string; containerId: null | string },
    session?: AbortSignal,
  ) => null | symbol;
  setOver: (
    over: {
      id: null | string;
      containerId: null | string;
      intent: DropIntent | null;
    },
    owner?: symbol,
  ) => void;
}

let releaseActive: (() => void) | undefined;

export const useDragOverStore = create<DragOverState>((set, get) => ({
  activeContainerId: null,
  activeId: null,
  activeOwner: null,
  intent: null,
  overContainerId: null,
  overId: null,
  reset: (owner) => {
    set((state) => {
      if (owner !== undefined && state.activeOwner !== owner) return state;
      releaseActive?.();
      releaseActive = undefined;
      return {
        activeContainerId: null,
        activeId: null,
        activeOwner: null,
        intent: null,
        overContainerId: null,
        overId: null,
      };
    });
  },
  setActive: ({ containerId, id }, session) => {
    if (session?.aborted) return null;
    const owner = Symbol(id ?? "drag");
    releaseActive?.();
    const onAbort = () => get().reset(owner);
    session?.addEventListener("abort", onAbort, { once: true });
    releaseActive = session ? () => session.removeEventListener("abort", onAbort) : undefined;
    set({
      activeContainerId: containerId,
      activeId: id,
      activeOwner: owner,
      intent: null,
      overContainerId: null,
      overId: null,
    });
    return owner;
  },
  setOver: ({ containerId, id, intent }, owner) => {
    set((state) => {
      if (owner !== undefined && state.activeOwner !== owner) return state;
      if (state.overId === id && state.overContainerId === containerId && state.intent === intent) {
        return state;
      }
      return { intent, overContainerId: containerId, overId: id };
    });
  },
}));

export function useIsCrossContainerDropTarget(containerId: string): boolean {
  return useDragOverStore(
    (s) =>
      s.activeContainerId !== null &&
      s.overContainerId === containerId &&
      s.activeContainerId !== containerId,
  );
}

export function useIsDropIntoFolder(folderId: string): boolean {
  return useDragOverStore(
    (s) =>
      s.activeId !== null &&
      ((s.overId === folderId && s.intent === "into") ||
        s.overContainerId === `parent:${folderId}`),
  );
}

export function useDropEdgeForRow(rowId: string): "above" | "below" | null {
  return useDragOverStore((s) => {
    if (s.activeId === null || s.overId !== rowId) return null;
    if (s.intent === "above") return "above";
    if (s.intent === "below") return "below";
    return null;
  });
}
