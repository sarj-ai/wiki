"use client";

import { useEffect } from "react";
import { create, type StateCreator } from "zustand";
import { persist, type StorageValue } from "zustand/middleware";

import { expandedDrivesStorageKey, expandedFoldersStorageKey } from "@/lib/browser-storage";
import { useBrowserUserId } from "@/lib/browser-user-context";
import { ZPERSISTED_DRIVE_EXPANSION, ZPERSISTED_EXPANSION } from "@/lib/storage-schemas";

interface FolderExpansionState {
  expanded: Record<string, true>;
  setExpanded: (id: string, next: boolean) => void;
}

interface DriveExpansionState {
  expanded: Record<string, true>;
  initialized: boolean;
  setExpanded: (id: string, next: boolean) => void;
}

type PersistedDriveExpansionState = Pick<DriveExpansionState, "expanded" | "initialized">;

const DRIVE_EXPANSION_VERSION = 1;

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

const driveInitializer: StateCreator<DriveExpansionState> = (set) => ({
  expanded: {},
  initialized: false,
  setExpanded: (id: string, next: boolean) =>
    set((state) => {
      if (next) {
        if (state.initialized && state.expanded[id]) return state;
        return { expanded: { ...state.expanded, [id]: true }, initialized: true };
      }
      if (state.initialized && !state.expanded[id]) return state;
      const { [id]: _removed, ...rest } = state.expanded;
      return { expanded: rest, initialized: true };
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
  if (!userId) return create<DriveExpansionState>()(driveInitializer);
  return create<DriveExpansionState>()(
    persist<DriveExpansionState, [], [], PersistedDriveExpansionState>(driveInitializer, {
      name: expandedDrivesStorageKey(userId),
      partialize: ({ expanded, initialized }) => ({ expanded, initialized }),
      storage: driveExpansionStorage(),
      version: DRIVE_EXPANSION_VERSION,
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

export function useExpandedDrive(
  id: string,
  defaultExpanded = false,
): [boolean, (next: boolean) => void] {
  const userId = useBrowserUserId();
  const store = driveStore(userId);
  const expanded = store((s) => s.expanded[id] === true);
  const initialized = store((s) => s.initialized);
  useEffect(() => {
    if (initialized || !defaultExpanded) return;
    initializeDefaultExpandedDrive(userId, id);
  }, [defaultExpanded, id, initialized, userId]);
  const setter = (next: boolean) => {
    setDriveExpanded(userId, id, next);
  };
  return [resolveDriveExpanded({ expanded, initialized }, defaultExpanded), setter];
}

export function initializeDefaultExpandedDrive(userId: null | string, driveId: string): void {
  const state = driveStore(userId).getState();
  if (state.initialized) return;
  state.setExpanded(driveId, true);
}

export function setDriveExpanded(userId: null | string, driveId: string, expanded: boolean): void {
  driveStore(userId).getState().setExpanded(driveId, expanded);
}

export function resolveDriveExpanded(
  state: { expanded: boolean; initialized: boolean },
  defaultExpanded: boolean,
): boolean {
  return state.initialized ? state.expanded : defaultExpanded;
}

export function expandPath(
  userId: null | string,
  driveId: string,
  folderIds: readonly string[],
): void {
  if (!userId) return;
  driveStore(userId).getState().setExpanded(driveId, true);
  const setExpanded = folderStore(userId).getState().setExpanded;
  for (const folderId of folderIds) {
    setExpanded(folderId, true);
  }
}

export function resetExpansionState(userId: null | string): void {
  const folders = userId ? folderStores.get(userId) : undefined;
  const drives = userId ? driveStores.get(userId) : undefined;
  folders?.setState({ expanded: {} });
  drives?.setState({ expanded: {}, initialized: false });
  if (userId) {
    folderStores.delete(userId);
    driveStores.delete(userId);
  }
  anonymousFolderStore.setState({ expanded: {} });
  anonymousDriveStore.setState({ expanded: {}, initialized: false });
  pendingReveal = null;
}

function driveExpansionStorage(): {
  getItem: (name: string) => null | StorageValue<PersistedDriveExpansionState>;
  setItem: (name: string, value: StorageValue<PersistedDriveExpansionState>) => void;
  removeItem: (name: string) => void;
} {
  return {
    getItem: (name) => {
      if (typeof window === "undefined") return null;
      const raw = window.localStorage.getItem(name);
      if (!raw) return null;
      const validated = ZPERSISTED_DRIVE_EXPANSION.safeParse(parseJsonOrNull(raw));
      if (!validated.success) return null;
      return {
        state: {
          expanded: validated.data.state.expanded,
          initialized: validated.data.state.initialized,
        },
        version: validated.data.version,
      };
    },
    removeItem: (name) => {
      if (typeof window === "undefined") return;
      window.localStorage.removeItem(name);
    },
    setItem: (name, value) => {
      if (typeof window === "undefined") return;
      window.localStorage.setItem(name, JSON.stringify(value));
    },
  };
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
  stateKey: "expanded",
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
      const validated = ZPERSISTED_EXPANSION.safeParse(parsed);
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
