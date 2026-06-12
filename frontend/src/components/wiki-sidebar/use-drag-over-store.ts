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
 * row whose drop indicator just turned on (and the one whose just
 * turned off) re-render, and the rest of the tree stays still.
 */

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

/**
 * Where the dragged row will land relative to the over row.
 *
 * - ``"into"`` — drop becomes a child of the over row. Only valid
 *   when the over row is a folder. Renders as a tint + ring on the
 *   folder header.
 * - ``"above"`` / ``"below"`` — drop reorders the dragged row above
 *   or below the over row at the over row's parent level. Renders as
 *   a 2px horizontal line.
 *
 * Computed in ``useWikiReorder.onDragOver`` from the cursor's Y
 * position on the over row's bounding box; thirds-zoning gives folder
 * rows an "into" middle band and edge bands for reorder, while
 * non-folder rows split the row in half (no into option).
 */
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
      // Avoid re-renders for no-op updates while the cursor is still
      // inside the same row at the same intent. onDragOver fires on
      // every pointer move, including pixel-scale jitter.
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

/**
 * True when the consumer's container is the drop target of an
 * in-flight cross-container drag — i.e. dragging from somewhere
 * else, currently hovering a row inside this container. Drives the
 * destination-drive header highlight.
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
 * True when the dragged row will land *inside* the given folder —
 * either because the cursor is on its header in the "into" middle
 * band, or because the cursor is on one of its children (cross-
 * container hover). Drives the folder-header tint.
 */
export function useIsDropIntoFolder(folderId: string): boolean {
  return useDragOverStore(
    (s) =>
      s.activeId !== null &&
      ((s.overId === folderId && s.intent === "into") ||
        s.overContainerId === `parent:${folderId}`),
  );
}

/**
 * True when the dragged row will land at this row id, with the given
 * intent ("above" or "below"). Used by SortableItem to render the
 * drop-indicator line precisely on the targeted edge.
 */
export function useDropEdgeForRow(rowId: string): "above" | "below" | null {
  return useDragOverStore((s) => {
    if (s.activeId === null || s.overId !== rowId) return null;
    if (s.intent === "above") return "above";
    if (s.intent === "below") return "below";
    return null;
  });
}
