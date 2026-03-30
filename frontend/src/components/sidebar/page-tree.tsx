"use client";

import { useState, useCallback, useMemo, useEffect, useRef } from "react";
import {
  DndContext,
  DragOverlay,
  closestCenter,
  PointerSensor,
  useSensor,
  useSensors,
  DragStartEvent,
  DragEndEvent,
  DragMoveEvent,
} from "@dnd-kit/core";
import { FileText } from "lucide-react";
import type { Page } from "@/types";
import { SidebarMenu } from "@/components/ui/sidebar";
import { PageTreeItem } from "./page-tree-item";
import { SidebarSkeleton } from "./sidebar-skeleton";

const EXPANDED_KEY = "sarj-expanded-ids";

interface FlatItem {
  page: Page;
  depth: number;
}

function flattenTree(pages: Page[], depth = 0, expandedIds: Set<string>): FlatItem[] {
  const result: FlatItem[] = [];
  for (const page of pages) {
    result.push({ page, depth });
    if (page.children.length > 0 && expandedIds.has(page.id)) {
      result.push(...flattenTree(page.children, depth + 1, expandedIds));
    }
  }
  return result;
}

interface PageTreeProps {
  pages: Page[];
  activePageId: string | null;
  onAddChild: (parentId: string) => void;
  onDelete: (pageId: string) => void;
  onRename: (pageId: string, newTitle: string) => void;
  onChangeIcon: (pageId: string, icon: string | null) => void;
  onMove?: (pageId: string, newParentId: string | null, newIndex: number) => void;
  renamingPageId?: string | null;
  onRenamingDone?: () => void;
  isLoading?: boolean;
  addingChildParentId?: string | null;
  movingPageId?: string | null;
  starredIds?: Set<string>;
  onToggleStar?: (pageId: string) => void;
  onImport?: (pageId: string) => void;
}

export function PageTree({
  pages,
  activePageId,
  onAddChild,
  onDelete,
  onRename,
  onChangeIcon,
  onMove,
  renamingPageId,
  onRenamingDone,
  isLoading,
  addingChildParentId,
  movingPageId,
  starredIds,
  onToggleStar,
  onImport,
}: PageTreeProps) {

  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());
  const [expandedHydrated, setExpandedHydrated] = useState(false);

  // Load from localStorage after mount to avoid hydration mismatch
  useEffect(() => {
    try {
      const stored = localStorage.getItem(EXPANDED_KEY);
      if (stored) setExpandedIds(new Set(JSON.parse(stored) as string[]));
    } catch {
      // ignore
    }
    setExpandedHydrated(true);
  }, []);

  // Persist to localStorage on change (skip initial mount)
  useEffect(() => {
    if (!expandedHydrated) return;
    try {
      localStorage.setItem(EXPANDED_KEY, JSON.stringify([...expandedIds]));
    } catch {
      // ignore
    }
  }, [expandedIds, expandedHydrated]);

  const [draggedId, setDraggedId] = useState<string | null>(null);
  const [dropTargetId, setDropTargetId] = useState<string | null>(null);
  const [dropPosition, setDropPosition] = useState<"before" | "after" | "inside">("after");
  const dropPositionRef = useRef<"before" | "after" | "inside">("after");

  const toggleExpanded = useCallback((id: string) => {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const flatItems = useMemo(
    () => flattenTree(pages, 0, expandedIds),
    [pages, expandedIds]
  );

  const sensors = useSensors(
    useSensor(PointerSensor, {
      activationConstraint: { distance: 8 },
    })
  );

  const draggedPage = draggedId
    ? flatItems.find((f) => f.page.id === draggedId)?.page ?? null
    : null;

  // Auto-expand parent chain to reveal a target page
  const expandAncestors = useCallback((targetId: string) => {
    const findAncestors = (nodes: Page[], id: string, ancestors: string[] = []): string[] | null => {
      for (const node of nodes) {
        if (node.id === id) return ancestors;
        if (node.children.length > 0) {
          const result = findAncestors(node.children, id, [...ancestors, node.id]);
          if (result) return result;
        }
      }
      return null;
    };
    const ancestors = findAncestors(pages, targetId);
    if (ancestors && ancestors.length > 0) {
      setExpandedIds(prev => {
        const next = new Set(prev);
        for (const id of ancestors) next.add(id);
        return next;
      });
    }
  }, [pages]);

  // Auto-expand when renaming a nested child
  useEffect(() => {
    if (renamingPageId) expandAncestors(renamingPageId);
  }, [renamingPageId, expandAncestors]);

  // Auto-expand when the active page is nested
  useEffect(() => {
    if (activePageId) expandAncestors(activePageId);
  }, [activePageId, expandAncestors]);

  // Listen for "expand section" events from the document page
  const [highlightedId, setHighlightedId] = useState<string | null>(null);
  const highlightTimerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => {
    function handleExpandSection(e: Event) {
      const detail = (e as CustomEvent).detail;
      const targetId = detail?.pageId as string;
      if (!targetId) return;

      // Expand the section + its ancestors
      setExpandedIds((prev) => {
        const next = new Set(prev);
        next.add(targetId);
        return next;
      });
      expandAncestors(targetId);

      // Highlight briefly
      setHighlightedId(targetId);
      clearTimeout(highlightTimerRef.current);
      highlightTimerRef.current = setTimeout(() => setHighlightedId(null), 1500);
    }
    window.addEventListener("sarj:expand-section", handleExpandSection);
    return () => {
      window.removeEventListener("sarj:expand-section", handleExpandSection);
      clearTimeout(highlightTimerRef.current);
    };
  }, [expandAncestors]);

  function handleDragStart(event: DragStartEvent) {
    setDraggedId(event.active.id as string);
  }

  function computeDropPosition(active: DragMoveEvent["active"], over: NonNullable<DragMoveEvent["over"]>): "before" | "after" | "inside" {
    const activeRect = active.rect.current.translated;
    const overRect = over.rect;
    if (activeRect && overRect) {
      const activeCenterY = activeRect.top + activeRect.height / 2;
      const topZone = overRect.top + overRect.height * 0.25;
      const bottomZone = overRect.top + overRect.height * 0.75;
      if (activeCenterY < topZone) return "before";
      if (activeCenterY > bottomZone) return "after";
      return "inside";
    }
    return "after";
  }

  function handleDragMove(event: DragMoveEvent) {
    const { over, active } = event;
    if (!over || over.id === active.id) {
      setDropTargetId(null);
      return;
    }

    setDropTargetId(over.id as string);

    const position = computeDropPosition(active, over);
    dropPositionRef.current = position;
    setDropPosition(position);
  }

  function handleDragEnd(event: DragEndEvent) {
    const { active, over } = event;
    setDraggedId(null);
    setDropTargetId(null);

    if (!over || active.id === over.id || !onMove) return;

    const movedPageId = active.id as string;
    const targetId = over.id as string;

    const targetIdx = flatItems.findIndex((f) => f.page.id === targetId);
    if (targetIdx === -1) return;

    const targetItem = flatItems[targetIdx];

    // Read from ref to avoid stale closure
    const position = dropPositionRef.current;

    if (position === "inside") {
      onMove(movedPageId, targetId, 0);
      setExpandedIds((prev) => new Set(prev).add(targetId));
    } else {
      const newParentId = targetItem.page.parentId;

      // Find siblings: either flat items at the same depth under the same parent,
      // or root-level pages when there is no parent
      const siblingIds = newParentId
        ? flatItems
            .filter((f) => f.page.parentId === newParentId && f.depth === targetItem.depth)
            .map((f) => f.page.id)
        : pages.map((p) => p.id);

      const siblingIdx = siblingIds.indexOf(targetId);
      const newIndex = position === "after" ? siblingIdx + 1 : siblingIdx;

      onMove(movedPageId, newParentId, newIndex);
    }
  }

  function handleDragCancel() {
    setDraggedId(null);
    setDropTargetId(null);
  }

  if (isLoading) {
    return <SidebarSkeleton />;
  }

  if (pages.length === 0) {
    return (
      <div className="px-4 py-3 text-sm text-muted-foreground">
        No pages yet. Create one to get started.
      </div>
    );
  }

  return (
    <DndContext
      sensors={sensors}
      collisionDetection={closestCenter}
      onDragStart={handleDragStart}
      onDragMove={handleDragMove}
      onDragEnd={handleDragEnd}
      onDragCancel={handleDragCancel}
    >
        <SidebarMenu>
          {flatItems.map((item) => (
            <PageTreeItem
              key={item.page.id}
              page={item.page}
              depth={item.depth}
              activePageId={activePageId}
              onAddChild={onAddChild}
              onDelete={onDelete}
              onRename={onRename}
              onChangeIcon={onChangeIcon}
              isExpanded={expandedIds.has(item.page.id)}
              onToggleExpand={toggleExpanded}
              isDragOver={dropTargetId === item.page.id}
              dropPosition={dropTargetId === item.page.id ? dropPosition : undefined}
              shouldAutoRename={renamingPageId === item.page.id}
              onRenamingDone={onRenamingDone}
              isAddingChild={addingChildParentId === item.page.id}
              isMoving={movingPageId === item.page.id}
              isHighlighted={highlightedId === item.page.id}
              isStarred={starredIds?.has(item.page.id)}
              onToggleStar={onToggleStar}
              onImport={onImport}
            />
          ))}
        </SidebarMenu>

      <DragOverlay dropAnimation={null}>
        {draggedPage && (
          <div className="flex items-center gap-1.5 rounded-md bg-sidebar-accent px-3 py-1.5 text-sm shadow-md">
            <span className="shrink-0 text-base leading-none">
              {draggedPage.icon || <FileText className="size-4" />}
            </span>
            <span className="truncate">{draggedPage.title}</span>
          </div>
        )}
      </DragOverlay>
    </DndContext>
  );
}
