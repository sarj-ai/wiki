"use client";

import { create } from "zustand";

/**
 * Tracks the live drag's active + over state during a sidebar drag.
 *
 * onDragOver from dnd-kit fires on every pointer move, so dropping
 * this state into React's render tree (Context, prop drilling, even
 * a shared useState) would re-render every TreeNode for every pixel
 * the cursor travels. zustand selectors only re-render the
 * subscribers whose selector return value actually changed — so the
 * row whose highlight just turned on (and the one whose just turned
 * off) re-render, and the rest of the tree stays still.
 *
 * State is intentionally minimal — just the id + container id pair
 * for active and over. Consumers compose the selectors they need
 * (e.g. "am I the cross-container drop target?", "am I being
 * hovered while collapsed?").
 */

interface DragOverState {
  activeId: string | null;
  activeContainerId: string | null;
  overId: string | null;
  overContainerId: string | null;
  setActive: (active: { id: string | null; containerId: string | null }) => void;
  setOver: (over: { id: string | null; containerId: string | null }) => void;
  reset: () => void;
}

export const useDragOverStore = create<DragOverState>((set) => ({
  activeId: null,
  activeContainerId: null,
  overId: null,
  overContainerId: null,
  setActive: ({ id, containerId }) => {
    set({ activeId: id, activeContainerId: containerId });
  },
  setOver: ({ id, containerId }) => {
    set((state) => {
      // Avoid re-renders for no-op updates while the cursor is still
      // inside the same row — onDragOver fires every pointer move.
      if (state.overId === id && state.overContainerId === containerId) return state;
      return { overId: id, overContainerId: containerId };
    });
  },
  reset: () => {
    set({
      activeId: null,
      activeContainerId: null,
      overId: null,
      overContainerId: null,
    });
  },
}));

/**
 * True when the consumer's container is the drop target of an
 * in-flight cross-container drag — i.e. dragging from somewhere
 * else, currently hovering a row inside this container. Drives
 * the destination-folder highlight.
 */
export function useIsCrossContainerDropTarget(containerId: string): boolean {
  return useDragOverStore(
    (s) =>
      s.activeContainerId !== null &&
      s.overContainerId === containerId &&
      s.activeContainerId !== containerId,
  );
}

/**
 * True when the consumer (a collapsed folder row) is itself being
 * hovered during an in-flight drag. Used to schedule auto-expand
 * after a brief dwell so the user can drop into its contents.
 */
export function useIsHoveredDuringDrag(rowId: string): boolean {
  return useDragOverStore((s) => s.activeId !== null && s.overId === rowId);
}
