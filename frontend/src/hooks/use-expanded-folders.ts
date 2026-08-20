"use client";

import { create, type StateCreator } from "zustand";
import { persist, type StorageValue } from "zustand/middleware";

import { collapsedDrivesStorageKey, expandedFoldersStorageKey } from "@/lib/browser-storage";
import { useBrowserUserId } from "@/lib/browser-user-context";
import { ZPersistedExpansion } from "@/lib/storage-schemas";

interface FolderExpansionState {
  expanded: Record<string, true>;
  setExpanded: (id: string, next: boolean) => void;
}

interface DriveCollapseState {
  collapsed: Record<string, true>;
  setCollapsed: (id: string, next: boolean) => void;
}

const folderInitializer: StateCreator<FolderExpansionState> = (set) => ({
  expanded: {},
  setExpanded: (id: string, next: boolean) =>
    set((state) => {
      if (next) {
        if (state.expanded[id]) return state;
        return { expanded: { ...state.expanded, [id]: true } };
      }
      if (!state.expanded[id]) return state;
      const { [id]: _removed, ...rest } = state.expanded;
      return { expanded: rest };
    }),
});

const driveInitializer: StateCreator<DriveCollapseState> = (set) => ({
  collapsed: {},
  setCollapsed: (id: string, next: boolean) =>
    set((state) => {
      if (next) {
        if (state.collapsed[id]) return state;
        return { collapsed: { ...state.collapsed, [id]: true } };
      }
      if (!state.collapsed[id]) return state;
      const { [id]: _removed, ...rest } = state.collapsed;
      return { collapsed: rest };
    }),
});

const folderStores = new Map<string, ReturnType<typeof createFolderStore>>();
const driveStores = new Map<string, ReturnType<typeof createDriveStore>>();
const anonymousFolderStore = createFolderStore(null);
const anonymousDriveStore = createDriveStore(null);

function createFolderStore(userId: null | string) {
  if (!userId) return create<FolderExpansionState>()(folderInitializer);
  return create<FolderExpansionState>()(
    persist(folderInitializer, {
      name: expandedFoldersStorageKey(userId),
      storage: validatedStorage("expanded", (map, version) => ({
        state: { expanded: map },
        version,
      })),
    }),
  );
}

function createDriveStore(userId: null | string) {
  if (!userId) return create<DriveCollapseState>()(driveInitializer);
  return create<DriveCollapseState>()(
    persist(driveInitializer, {
      name: collapsedDrivesStorageKey(userId),
      storage: validatedStorage("collapsed", (map, version) => ({
        state: { collapsed: map },
        version,
      })),
    }),
  );
}

function folderStore(userId: null | string) {
  if (!userId) return anonymousFolderStore;
  const existing = folderStores.get(userId);
  if (existing) return existing;
  const created = createFolderStore(userId);
  folderStores.set(userId, created);
  return created;
}

function driveStore(userId: null | string) {
  if (!userId) return anonymousDriveStore;
  const existing = driveStores.get(userId);
  if (existing) return existing;
  const created = createDriveStore(userId);
  driveStores.set(userId, created);
  return created;
}

let pendingReveal: null | string = null;

export function useExpandedFolder(id: string): [boolean, (next: boolean) => void] {
  const store = folderStore(useBrowserUserId());
  const expanded = store((s) => s.expanded[id] === true);
  const setExpanded = store((s) => s.setExpanded);
  const setter = (next: boolean) => {
    setExpanded(id, next);
  };
  return [expanded, setter];
}

export function useExpandedDrive(id: string): [boolean, (next: boolean) => void] {
  const store = driveStore(useBrowserUserId());
  const collapsed = store((s) => s.collapsed[id] === true);
  const setCollapsed = store((s) => s.setCollapsed);
  const setter = (next: boolean) => {
    setCollapsed(id, !next);
  };
  return [!collapsed, setter];
}

export function expandPath(
  userId: null | string,
  driveId: string,
  folderIds: readonly string[],
): void {
  if (!userId) return;
  driveStore(userId).getState().setCollapsed(driveId, false);
  const setExpanded = folderStore(userId).getState().setExpanded;
  for (const folderId of folderIds) {
    setExpanded(folderId, true);
  }
}

export function resetExpansionState(userId: null | string): void {
  const folders = userId ? folderStores.get(userId) : undefined;
  const drives = userId ? driveStores.get(userId) : undefined;
  folders?.setState({ expanded: {} });
  drives?.setState({ collapsed: {} });
  if (userId) {
    folderStores.delete(userId);
    driveStores.delete(userId);
  }
  anonymousFolderStore.setState({ expanded: {} });
  anonymousDriveStore.setState({ collapsed: {} });
  pendingReveal = null;
}

export function requestReveal(fileId: string): void {
  pendingReveal = fileId;
}

export function consumeRevealFor(fileId: string): boolean {
  if (pendingReveal !== fileId) return false;
  pendingReveal = null;
  return true;
}

function validatedStorage<S>(
  stateKey: "collapsed" | "expanded",
  toValue: (map: Record<string, true>, version: number) => StorageValue<S>,
): {
  getItem: (name: string) => null | StorageValue<S>;
  setItem: (name: string, value: StorageValue<S>) => void;
  removeItem: (name: string) => void;
} {
  return {
    getItem: (name: string) => {
      if (typeof window === "undefined") return null;
      const raw = window.localStorage.getItem(name);
      if (!raw) return null;
      const parsed = parseJsonOrNull(raw);
      const validated = ZPersistedExpansion.safeParse(parsed);
      if (!validated.success) return null;
      return toValue(validated.data.state[stateKey] ?? {}, validated.data.version);
    },
    removeItem: (name: string) => {
      if (typeof window === "undefined") return;
      window.localStorage.removeItem(name);
    },
    setItem: (name: string, value: StorageValue<S>) => {
      if (typeof window === "undefined") return;
      window.localStorage.setItem(name, JSON.stringify(value));
    },
  };
}

function parseJsonOrNull(raw: string): unknown {
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}
