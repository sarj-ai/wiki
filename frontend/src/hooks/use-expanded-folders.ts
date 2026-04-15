"use client";

import { useCallback, useEffect, useState } from "react";

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
    subscribers.forEach((fn) => fn());
  }

  function setMembership(id: string, member: boolean) {
    const next = new Set(ensureInit());
    if (member) next.add(id);
    else next.delete(id);
    current = next;
    persist(next);
    notify();
  }

  function useEntry(id: string): [boolean, (next: boolean) => void] {
    const [, forceRerender] = useState(0);

    useEffect(() => {
      const callback = () => forceRerender((n) => n + 1);
      subscribers.add(callback);
      return () => {
        subscribers.delete(callback);
      };
    }, []);

    const member = ensureInit().has(id);

    const setter = useCallback(
      (next: boolean) => {
        setMembership(id, next);
      },
      [id],
    );

    return [member, setter];
  }

  return { useEntry };
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
