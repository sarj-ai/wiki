"use client";

import { useCallback } from "react";
import { create } from "zustand";
import { persist, type StorageValue } from "zustand/middleware";

import { ZPersistedExpansion } from "@/lib/storage-schemas";

interface FolderExpansionState {
  expanded: Record<string, true>;
  setExpanded: (id: string, next: boolean) => void;
}

interface DriveCollapseState {
  collapsed: Record<string, true>;
  setCollapsed: (id: string, next: boolean) => void;
}

const useFolderStore = create<FolderExpansionState>()(
  persist(
    (set) => ({
      expanded: {},
      setExpanded: (id, next) =>
        set((state) => {
          if (next) {
            if (state.expanded[id]) return state;
            return { expanded: { ...state.expanded, [id]: true } };
          }
          if (!state.expanded[id]) return state;
          const { [id]: _removed, ...rest } = state.expanded;
          return { expanded: rest };
        }),
    }),
    {
      name: "wiki-expanded-folders",
      storage: legacyAwareStorage("expanded"),
    },
  ),
);

const useDriveCollapseStore = create<DriveCollapseState>()(
  persist(
    (set) => ({
      collapsed: {},
      setCollapsed: (id, next) =>
        set((state) => {
          if (next) {
            if (state.collapsed[id]) return state;
            return { collapsed: { ...state.collapsed, [id]: true } };
          }
          if (!state.collapsed[id]) return state;
          const { [id]: _removed, ...rest } = state.collapsed;
          return { collapsed: rest };
        }),
    }),
    {
      name: "wiki-collapsed-drives",
      storage: legacyAwareStorage("collapsed"),
    },
  ),
);

let pendingReveal: string | null = null;

function legacyAwareStorage<S>(stateKey: "expanded" | "collapsed"): {
  getItem: (name: string) => StorageValue<S> | null;
  setItem: (name: string, value: StorageValue<S>) => void;
  removeItem: (name: string) => void;
} {
  return {
    getItem: (name) => {
      if (typeof window === "undefined") return null;
      const raw = window.localStorage.getItem(name);
      if (!raw) return null;
      try {
        const parsed: unknown = JSON.parse(raw);
        if (Array.isArray(parsed)) {
          const map: Record<string, true> = {};
          for (const id of parsed) {
            if (typeof id === "string") map[id] = true;
          }
          return { state: { [stateKey]: map } as S, version: 0 };
        }
        const validated = ZPersistedExpansion.safeParse(parsed);
        if (!validated.success) return null;
        return validated.data as unknown as StorageValue<S>;
      } catch {
        return null;
      }
    },
    setItem: (name, value) => {
      if (typeof window === "undefined") return;
      window.localStorage.setItem(name, JSON.stringify(value));
    },
    removeItem: (name) => {
      if (typeof window === "undefined") return;
      window.localStorage.removeItem(name);
    },
  };
}

export function useExpandedFolder(id: string): [boolean, (next: boolean) => void] {
  const expanded = useFolderStore((s) => s.expanded[id] === true);
  const setExpanded = useFolderStore((s) => s.setExpanded);
  const setter = useCallback(
    (next: boolean) => {
      setExpanded(id, next);
    },
    [id, setExpanded],
  );
  return [expanded, setter];
}

export function useExpandedDrive(id: string): [boolean, (next: boolean) => void] {
  const collapsed = useDriveCollapseStore((s) => s.collapsed[id] === true);
  const setCollapsed = useDriveCollapseStore((s) => s.setCollapsed);
  const setter = useCallback(
    (next: boolean) => {
      setCollapsed(id, !next);
    },
    [id, setCollapsed],
  );
  return [!collapsed, setter];
}

export function expandPath(driveId: string, folderIds: readonly string[]): void {
  useDriveCollapseStore.getState().setCollapsed(driveId, false);
  const setExpanded = useFolderStore.getState().setExpanded;
  for (const folderId of folderIds) {
    setExpanded(folderId, true);
  }
}

export function setFolderExpanded(folderId: string, next: boolean): void {
  useFolderStore.getState().setExpanded(folderId, next);
}

export function setDriveExpanded(driveId: string, next: boolean): void {
  useDriveCollapseStore.getState().setCollapsed(driveId, !next);
}

export function requestReveal(fileId: string): void {
  pendingReveal = fileId;
}

export function consumeRevealFor(fileId: string): boolean {
  if (pendingReveal !== fileId) return false;
  pendingReveal = null;
  return true;
}
