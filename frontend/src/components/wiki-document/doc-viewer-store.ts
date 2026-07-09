"use client";

import { create } from "zustand";

export interface ActiveDoc {
  fileId: string;
  mimeType: string;
  title: string;
  reloadKey: number;
}

interface DocViewerState {
  active: ActiveDoc | null;
  prewarm: ActiveDoc | null;
  warm: ActiveDoc[];
  slot: HTMLElement | null;
  /** Live Google editor is showing (docs only). Owned by the host; toolbar toggles it. */
  editing: boolean;
  /** Bumped each time Edit is entered so the host can re-show the load skeleton. */
  editGeneration: number;
  /** Comments side panel open (docs only, view mode). Owned by the host; toolbar toggles it. */
  showComments: boolean;
  setActive: (active: ActiveDoc | null) => void;
  setPrewarm: (prewarm: ActiveDoc | null) => void;
  setWarm: (warm: ActiveDoc[]) => void;
  setSlot: (slot: HTMLElement | null) => void;
  setEditing: (editing: boolean) => void;
  setShowComments: (show: boolean) => void;
}

export const useDocViewerStore = create<DocViewerState>((set) => ({
  active: null,
  prewarm: null,
  warm: [],
  slot: null,
  editing: false,
  editGeneration: 0,
  showComments: false,
  setActive: (active) => {
    set((state) => {
      const switched = state.active?.fileId !== active?.fileId;
      return {
        active,
        // New file (or clear): drop edit/comments chrome so it doesn't leak across docs.
        ...(switched ? { editing: false, showComments: false } : {}),
      };
    });
  },
  setPrewarm: (prewarm) => {
    set((state) => (state.prewarm?.fileId === prewarm?.fileId ? state : { ...state, prewarm }));
  },
  setWarm: (warm) => {
    set({ warm });
  },
  setSlot: (slot) => {
    set({ slot });
  },
  setEditing: (editing) => {
    set((state) => ({
      editing,
      ...(editing ? { showComments: false, editGeneration: state.editGeneration + 1 } : {}),
    }));
  },
  setShowComments: (showComments) => {
    set({ showComments });
  },
}));
