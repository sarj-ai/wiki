"use client";

import { useCallback, useSyncExternalStore } from "react";

/**
 * Module-level persistent Set backed by localStorage. Shared across
 * every component using the same store, so toggling an entry in one
 * subtree re-renders all subscribers without an explicit props path.
 * Used twice in this file: once for expanded folder ids (default
 * collapsed) and once for collapsed drive ids (default expanded).
 */
function createPersistedSet(storageKey: string) {
  const subscribers = new Set<() => void>();
  let current: Set<string> | null = null;

  function ensureInit(): Set<string> {
    if (current !== null) return current;
    if (typeof window === "undefined") {
      current = new Set();
      return current;
    }
    try {
      const raw = window.localStorage.getItem(storageKey);
      if (!raw) {
        current = new Set();
        return current;
      }
      const parsed = JSON.parse(raw) as unknown;
      if (!Array.isArray(parsed)) {
        current = new Set();
        return current;
      }
      current = new Set(parsed.filter((id): id is string => typeof id === "string"));
      return current;
    } catch {
      current = new Set();
      return current;
    }
  }

  function persist(set: Set<string>) {
    if (typeof window === "undefined") return;
    window.localStorage.setItem(storageKey, JSON.stringify([...set]));
  }

  function notify() {
    subscribers.forEach((fn) => { fn(); });
  }

  function setMembership(id: string, member: boolean) {
    const next = new Set(ensureInit());
    if (member) next.add(id);
    else next.delete(id);
    current = next;
    persist(next);
    notify();
  }

  function subscribe(cb: () => void): () => void {
    subscribers.add(cb);
    return () => {
      subscribers.delete(cb);
    };
  }

  function useEntry(id: string): [boolean, (next: boolean) => void] {
    // useSyncExternalStore is the React-blessed way to subscribe to
    // mutable module state. React Compiler treats it as a black box,
    // so it never caches the returned snapshot across an external
    // mutation — which a hand-rolled forceRerender pattern does NOT
    // guarantee under the compiler.
    const getSnapshot = useCallback(() => ensureInit().has(id), [id]);
    const getServerSnapshot = useCallback(() => false, []);
    const member = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);

    const setter = useCallback(
      (next: boolean) => {
        setMembership(id, next);
      },
      [id],
    );

    return [member, setter];
  }

  return { useEntry, setMembership };
}

// Folders default to COLLAPSED — the set stores folder ids the user
// has explicitly expanded.
const folderStore = createPersistedSet("navigator-expanded-folders");

// Drives default to EXPANDED — the set stores drive ids the user has
// explicitly collapsed, so a fresh visit still shows every drive.
const driveCollapseStore = createPersistedSet("navigator-collapsed-drives");

/**
 * Expanded state for a single folder id. Default: collapsed.
 * Persisted across reloads via localStorage.
 */
export function useExpandedFolder(id: string): [boolean, (next: boolean) => void] {
  return folderStore.useEntry(id);
}

/**
 * Expanded state for a drive (top-level workspace). Default: expanded.
 * Persisted across reloads via localStorage.
 */
export function useExpandedDrive(id: string): [boolean, (next: boolean) => void] {
  const [collapsed, setCollapsed] = driveCollapseStore.useEntry(id);
  const expanded = !collapsed;
  const setExpanded = useCallback(
    (next: boolean) => {
      setCollapsed(!next);
    },
    [setCollapsed],
  );
  return [expanded, setExpanded];
}

/**
 * Imperative reveal — expand the drive plus every folder along the
 * supplied chain. Used by the auto-reveal effect so opening a file
 * via the command palette walks the sidebar tree open to where the
 * file lives. Safe to call from a React effect; backed by the same
 * persisted Sets as the hook entries above.
 */
export function expandPath(driveId: string, folderIds: readonly string[]): void {
  driveCollapseStore.setMembership(driveId, false);
  for (const folderId of folderIds) {
    folderStore.setMembership(folderId, true);
  }
}
