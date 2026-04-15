"use client";

import { useCallback, useEffect, useState } from "react";

const STORAGE_KEY = "sarj-collapsed-sections";

/**
 * Manages which sidebar sections (workspace IDs or "private") are collapsed.
 * State is persisted to localStorage and hydrated after mount to avoid SSR
 * hydration mismatches.
 */
export function useCollapsedSections() {
  const [collapsedSections, setCollapsedSections] = useState<Set<string>>(new Set());
  const [hydrated, setHydrated] = useState(false);

  // Load from localStorage after mount to avoid hydration mismatch
  useEffect(() => {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      if (stored) {
        setCollapsedSections(new Set(JSON.parse(stored) as string[]));
      }
    } catch {
      // ignore
    }
    setHydrated(true);
  }, []);

  // Persist to localStorage on change (skip initial mount)
  useEffect(() => {
    if (!hydrated) return;
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify([...collapsedSections]));
    } catch {
      // ignore
    }
  }, [collapsedSections, hydrated]);

  const toggleSection = useCallback((id: string) => {
    setCollapsedSections((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const uncollapseSection = useCallback((id: string) => {
    setCollapsedSections((prev) => {
      if (!prev.has(id)) return prev;
      const next = new Set(prev);
      next.delete(id);
      return next;
    });
  }, []);

  const collapseAll = useCallback((sectionIds: string[]) => {
    setCollapsedSections(new Set(sectionIds));
  }, []);

  const expandAll = useCallback(() => {
    setCollapsedSections(new Set());
  }, []);

  return {
    collapsedSections,
    toggleSection,
    uncollapseSection,
    collapseAll,
    expandAll,
    hydrated,
  } as const;
}
