"use client";

import { useSyncExternalStore } from "react";
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

type FeedbackKind = "container" | "folder" | "row";

const FEEDBACK_LISTENERS: Record<FeedbackKind, Map<string, Set<() => void>>> = {
  container: new Map(),
  folder: new Map(),
  row: new Map(),
};
let feedbackListenerCount = 0;
let releaseFeedback: (() => void) | undefined;

function subscribeFeedback(kind: FeedbackKind, id: string, listener: () => void): () => void {
  const listeners = FEEDBACK_LISTENERS[kind];
  const bucket = listeners.get(id) ?? new Set<() => void>();
  listeners.set(id, bucket);
  bucket.add(listener);
  feedbackListenerCount++;
  releaseFeedback ??= useDragOverStore.subscribe(notifyFeedback);
  return () => {
    if (!bucket.delete(listener)) return;
    feedbackListenerCount--;
    if (bucket.size === 0) listeners.delete(id);
    if (feedbackListenerCount === 0) {
      releaseFeedback?.();
      releaseFeedback = undefined;
    }
  };
}

function notifyFeedback(state: DragOverState, previous: DragOverState): void {
  const rowIds = new Set([previous.overId, state.overId]);
  for (const id of rowIds) {
    if (id === null) continue;
    if (rowDropEdge(state, id) !== rowDropEdge(previous, id)) notifyForId("row", id);
  }
  const containerIds = new Set([previous.overContainerId, state.overContainerId]);
  for (const id of containerIds) {
    if (id === null) continue;
    if (crossContainerDrop(state, id) !== crossContainerDrop(previous, id)) {
      notifyForId("container", id);
    }
    if (id.startsWith("parent:")) rowIds.add(id.slice("parent:".length));
  }
  for (const id of rowIds) {
    if (id === null) continue;
    if (folderDrop(state, id) !== folderDrop(previous, id)) notifyForId("folder", id);
  }
}

function notifyForId(kind: FeedbackKind, id: string): void {
  const listeners = FEEDBACK_LISTENERS[kind].get(id);
  if (!listeners) return;
  for (const listener of listeners) listener();
}

function crossContainerDrop(state: DragOverState, containerId: string): boolean {
  return (
    state.activeContainerId !== null &&
    state.overContainerId === containerId &&
    state.activeContainerId !== containerId
  );
}

function folderDrop(state: DragOverState, folderId: string): boolean {
  return (
    state.activeId !== null &&
    ((state.overId === folderId && state.intent === "into") ||
      state.overContainerId === `parent:${folderId}`)
  );
}

function rowDropEdge(state: DragOverState, rowId: string): "above" | "below" | null {
  if (state.activeId === null || state.overId !== rowId) return null;
  if (state.intent === "above") return "above";
  if (state.intent === "below") return "below";
  return null;
}

export function useIsCrossContainerDropTarget(containerId: string): boolean {
  return useSyncExternalStore(
    (listener) => subscribeFeedback("container", containerId, listener),
    () => crossContainerDrop(useDragOverStore.getState(), containerId),
    () => false,
  );
}

export function useIsDropIntoFolder(folderId: string): boolean {
  return useSyncExternalStore(
    (listener) => subscribeFeedback("folder", folderId, listener),
    () => folderDrop(useDragOverStore.getState(), folderId),
    () => false,
  );
}

export function useDropEdgeForRow(rowId: string): "above" | "below" | null {
  return useSyncExternalStore(
    (listener) => subscribeFeedback("row", rowId, listener),
    () => rowDropEdge(useDragOverStore.getState(), rowId),
    () => null,
  );
}
