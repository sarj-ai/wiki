"use client";

import { create } from "zustand";

interface DragOverState {
  activeContainerId: null | string;
  activeId: null | string;
  intent: DropIntent | null;
  overContainerId: null | string;
  overId: null | string;
  reset: () => void;
  setActive: (active: { id: null | string; containerId: null | string }) => void;
  setOver: (over: {
    id: null | string;
    containerId: null | string;
    intent: DropIntent | null;
  }) => void;
}

export type DropIntent = "above" | "below" | "into";

export const useDragOverStore = create<DragOverState>((set) => ({
  activeContainerId: null,
  activeId: null,
  intent: null,
  overContainerId: null,
  overId: null,
  reset: () => {
    set({
      activeContainerId: null,
      activeId: null,
      intent: null,
      overContainerId: null,
      overId: null,
    });
  },
  setActive: ({ containerId, id }) => {
    set({ activeContainerId: containerId, activeId: id });
  },
  setOver: ({ containerId, id, intent }) => {
    set((state) => {
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
