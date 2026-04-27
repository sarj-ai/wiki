"use client";

import { useCallback } from "react";
import { create } from "zustand";
import { persist, type StorageValue } from "zustand/middleware";

/**
 * Per-row expansion state for the navigator sidebar, persisted to
 * localStorage so a reload doesn't collapse everything the user just
 * opened. Two stores: one for folder ids the user has *expanded*
 * (default collapsed), one for drive ids the user has *collapsed*
 * (default expanded). Storing the negation for drives keeps "fresh
 * visit shows every drive" working without inventing a per-drive
 * default at write time.
 *
 * State shape is ``Record<string, true>`` rather than a Set so
 * persist's JSON serialiser works without a custom replacer/reviver,
 * and so React Compiler / consumers see plain primitives. zustand's
 * subscribe path uses ``useSyncExternalStore`` internally, which the
 * compiler treats as a black-box signal — same fix the previous
 * hand-rolled version landed in 6b8ebc9.
 */

interface FolderExpansionState {
  expanded: Record<string, true>;
  setExpanded: (id: string, next: boolean) => void;
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
      name: "navigator-expanded-folders",
      storage: legacyAwareStorage("expanded"),
    },
  ),
);

interface DriveCollapseState {
  collapsed: Record<string, true>;
  setCollapsed: (id: string, next: boolean) => void;
}

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
      name: "navigator-collapsed-drives",
      storage: legacyAwareStorage("collapsed"),
    },
  ),
);

/**
 * Expanded state for a single folder id. Default: collapsed.
 * Persisted across reloads via localStorage.
 */
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

/**
 * Expanded state for a drive (top-level workspace). Default: expanded.
 * Persisted across reloads via localStorage.
 */
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

/**
 * Imperative reveal — expand the drive plus every folder along the
 * supplied chain. Used by the auto-reveal effect so opening a file
 * via the command palette walks the sidebar tree open to where the
 * file lives.
 */
export function expandPath(driveId: string, folderIds: readonly string[]): void {
  useDriveCollapseStore.getState().setCollapsed(driveId, false);
  const setExpanded = useFolderStore.getState().setExpanded;
  for (const folderId of folderIds) {
    setExpanded(folderId, true);
  }
}

// One-shot reveal-request handle. The command palette's "open" path
// asks the sidebar to walk its tree open to a specific file id; the
// sidebar consumes the request once the breadcrumb data arrives,
// then clears it. Manual sidebar nav and browser back/forward never
// set it, so the user's collapse state survives those.
let pendingReveal: string | null = null;

export function requestReveal(fileId: string): void {
  pendingReveal = fileId;
}

export function consumeRevealFor(fileId: string): boolean {
  if (pendingReveal !== fileId) return false;
  pendingReveal = null;
  return true;
}

/**
 * Storage adapter that transparently migrates pre-zustand entries.
 * The previous hand-rolled store wrote a bare JSON array of ids; the
 * new state shape wraps them in ``{ <key>: { id1: true, … } }``.
 * Detects the legacy shape on read and rewraps it into zustand's
 * ``StorageValue`` envelope so existing users don't lose their
 * expansion state on the swap.
 */
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
        return parsed as StorageValue<S>;
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
