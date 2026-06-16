"use client";

import { useEffect, useRef, useState, type KeyboardEvent, type RefObject } from "react";

import { setDriveExpanded, setFolderExpanded } from "@/hooks/use-expanded-folders";

const TREE_ITEM_PREFIX = "treeitem-";
const TYPEAHEAD_RESET_MS = 600;

function treeItemDomId(rowId: string): string {
  return `${TREE_ITEM_PREFIX}${rowId}`;
}

export function treeRowProps(rowId: string, level: number, kind: "drive" | "folder" | "file") {
  return {
    id: treeItemDomId(rowId),
    role: "treeitem" as const,
    tabIndex: -1,
    "aria-level": level,
    "data-row-kind": kind,
  };
}

interface SidebarTreeNav {
  ref: RefObject<HTMLDivElement | null>;
  role: "tree";
  tabIndex: 0;
  "aria-activedescendant": string | undefined;
  "aria-label": string;
  onKeyDown: (event: KeyboardEvent<HTMLDivElement>) => void;
  onFocus: () => void;
}

export function useSidebarKeyboardNav(activeRowId: string | null): SidebarTreeNav {
  const ref = useRef<HTMLDivElement | null>(null);
  const typeahead = useRef({ buffer: "", at: 0 });
  const [focusedId, setFocusedId] = useState<string | null>(null);

  useEffect(() => {
    const container = ref.current;
    if (!container) return;
    for (const previous of container.querySelectorAll("[data-kbd-focused]")) {
      previous.removeAttribute("data-kbd-focused");
    }
    if (!focusedId) return;
    const el = container.querySelector<HTMLElement>(`#${CSS.escape(treeItemDomId(focusedId))}`);
    el?.setAttribute("data-kbd-focused", "");
    el?.scrollIntoView({ block: "nearest" });
  }, [focusedId]);

  const visibleRows = (): HTMLElement[] =>
    ref.current ? Array.from(ref.current.querySelectorAll<HTMLElement>('[role="treeitem"]')) : [];

  const focusAt = (rows: HTMLElement[], index: number): void => {
    const el = rows[index];
    if (el) setFocusedId(rowIdOf(el));
  };

  const expandOrEnter = (rows: HTMLElement[], index: number): void => {
    const el = rows[index];
    if (!el) {
      focusAt(rows, 0);
      return;
    }
    if (!isExpandable(el)) return;
    if (isExpanded(el)) focusAt(rows, Math.min(index + 1, rows.length - 1));
    else toggleRow(el, true);
  };

  const collapseOrLeave = (rows: HTMLElement[], index: number): void => {
    const el = rows[index];
    if (!el) return;
    if (isExpandable(el) && isExpanded(el)) {
      toggleRow(el, false);
      return;
    }
    const parent = findParent(rows, index);
    if (parent >= 0) focusAt(rows, parent);
  };

  const actions: Record<string, (rows: HTMLElement[], index: number) => void> = {
    ArrowDown: (rows, index) => {
      focusAt(rows, index < 0 ? 0 : Math.min(index + 1, rows.length - 1));
    },
    ArrowUp: (rows, index) => {
      focusAt(rows, index <= 0 ? 0 : index - 1);
    },
    Home: (rows) => {
      focusAt(rows, 0);
    },
    End: (rows) => {
      focusAt(rows, rows.length - 1);
    },
    ArrowRight: expandOrEnter,
    ArrowLeft: collapseOrLeave,
    Enter: (rows, index) => rows[index]?.click(),
    " ": (rows, index) => rows[index]?.click(),
  };

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>): void => {
    const rows = visibleRows();
    if (rows.length === 0) return;
    const index = focusedId ? rows.findIndex((el) => rowIdOf(el) === focusedId) : -1;
    const action = actions[event.key];
    if (action) {
      event.preventDefault();
      action(rows, index);
      return;
    }
    if (event.key.length === 1 && !event.metaKey && !event.ctrlKey && !event.altKey) {
      event.preventDefault();
      typeaheadTo(rows, index, event.key);
    }
  };

  const typeaheadTo = (rows: HTMLElement[], index: number, char: string): void => {
    const state = typeahead.current;
    const now = Date.now();
    state.buffer = now - state.at > TYPEAHEAD_RESET_MS ? char : state.buffer + char;
    state.at = now;
    const needle = state.buffer.toLowerCase();
    for (let step = 1; step <= rows.length; step += 1) {
      const candidate = rows[(Math.max(index, 0) + step) % rows.length];
      if (!candidate) continue;
      if (candidate.textContent.trim().toLowerCase().startsWith(needle)) {
        setFocusedId(rowIdOf(candidate));
        return;
      }
    }
  };

  const onFocus = (): void => {
    if (focusedId) return;
    const rows = visibleRows();
    const activeIndex = activeRowId ? rows.findIndex((el) => rowIdOf(el) === activeRowId) : -1;
    focusAt(rows, activeIndex >= 0 ? activeIndex : 0);
  };

  return {
    ref,
    role: "tree",
    tabIndex: 0,
    "aria-activedescendant": focusedId ? treeItemDomId(focusedId) : undefined,
    "aria-label": "Drive tree",
    onKeyDown,
    onFocus,
  };
}

function rowIdOf(el: HTMLElement): string {
  return el.id.slice(TREE_ITEM_PREFIX.length);
}

function isExpandable(el: HTMLElement): boolean {
  return el.hasAttribute("aria-expanded");
}

function isExpanded(el: HTMLElement): boolean {
  return el.getAttribute("aria-expanded") === "true";
}

function toggleRow(el: HTMLElement, next: boolean): void {
  const rowId = rowIdOf(el);
  if (el.dataset["rowKind"] === "drive") setDriveExpanded(rowId, next);
  else setFolderExpanded(rowId, next);
}

function findParent(rows: HTMLElement[], index: number): number {
  const level = Number(rows[index]?.getAttribute("aria-level") ?? "0");
  for (let i = index - 1; i >= 0; i -= 1) {
    if (Number(rows[i]?.getAttribute("aria-level") ?? "0") < level) return i;
  }
  return -1;
}
