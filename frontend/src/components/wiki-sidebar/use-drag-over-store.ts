"use client";

import { create } from "zustand";

interface DragOverState {
  activeId: string | null;
  activeContainerId: string | null;
  overId: string | null;
  overContainerId: string | null;
  intent: DropIntent | null;
  setActive: (active: { id: string | null; containerId: string | null }) => void;
  setOver: (over: {
    id: string | null;
    containerId: string | null;
    intent: DropIntent | null;
  }) => void;
  reset: () => void;
}

export type DropIntent = "into" | "above" | "below";

export const useDragOverStore = create<DragOverState>((set) => ({
  activeId: null,
  activeContainerId: null,
  overId: null,
  overContainerId: null,
  intent: null,
  setActive: ({ id, containerId }) => {
    set({ activeId: id, activeContainerId: containerId });
  },
  setOver: ({ id, containerId, intent }) => {
    set((state) => {
      if (state.overId === id && state.overContainerId === containerId && state.intent === intent) {
        return state;
      }
      return { overId: id, overContainerId: containerId, intent };
    });
  },
  reset: () => {
    set({
      activeId: null,
      activeContainerId: null,
      overId: null,
      overContainerId: null,
      intent: null,
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
