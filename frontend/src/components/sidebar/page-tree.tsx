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
  onPageSelect: (pageId: string) => void;
  onAddChild: (parentId: string) => void;
  onDelete: (pageId: string) => void;
  onRename: (pageId: string, newTitle: string) => void;
  onChangeIcon: (pageId: string, icon: string | null) => void;
  onMove?: (pageId: string, newParentId: string | null, newIndex: number) => void;
  renamingPageId?: string | null;
  onRenamingDone?: () => void;
  loading?: boolean;
}

export function PageTree({
  pages,
  activePageId,
  onPageSelect,
  onAddChild,
  onDelete,
  onRename,
  onChangeIcon,
  onMove,
  renamingPageId,
  onRenamingDone,
  loading,
}: PageTreeProps) {
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());
  const [activeId, setActiveId] = useState<string | null>(null);
  const [overId, setOverId] = useState<string | null>(null);
  const [dropIntent, setDropIntent] = useState<"before" | "after" | "inside">("after");
  const dropIntentRef = useRef<"before" | "after" | "inside">("after");

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

  const activePage = activeId
    ? flatItems.find((f) => f.page.id === activeId)?.page ?? null
    : null;

  // Auto-expand parent chain when renamingPageId targets a nested child
  useEffect(() => {
    if (!renamingPageId) return;
    const findAncestors = (nodes: Page[], targetId: string, ancestors: string[] = []): string[] | null => {
      for (const node of nodes) {
        if (node.id === targetId) return ancestors;
        if (node.children.length > 0) {
          const result = findAncestors(node.children, targetId, [...ancestors, node.id]);
          if (result) return result;
        }
      }
      return null;
    };
    const ancestors = findAncestors(pages, renamingPageId);
    if (ancestors && ancestors.length > 0) {
      setExpandedIds(prev => {
        const next = new Set(prev);
        for (const id of ancestors) next.add(id);
        return next;
      });
    }
  }, [renamingPageId, pages]);

  function handleDragStart(event: DragStartEvent) {
    setActiveId(event.active.id as string);
  }

  function computeIntent(active: DragMoveEvent["active"], over: NonNullable<DragMoveEvent["over"]>): "before" | "after" | "inside" {
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
      setOverId(null);
      return;
    }

    setOverId(over.id as string);

    const intent = computeIntent(active, over);
    dropIntentRef.current = intent;
    setDropIntent(intent);
  }

  function handleDragEnd(event: DragEndEvent) {
    const { active, over } = event;
    setActiveId(null);
    setOverId(null);

    if (!over || active.id === over.id || !onMove) return;

    const draggedId = active.id as string;
    const targetId = over.id as string;

    // Find the target item in the flat list
    const targetIdx = flatItems.findIndex((f) => f.page.id === targetId);
    if (targetIdx === -1) return;

    const targetItem = flatItems[targetIdx];

    // Read from ref to avoid stale closure
    const intent = dropIntentRef.current;

    if (intent === "inside") {
      // Re-parent: make dragged page a child of target
      onMove(draggedId, targetId, 0);
      // Auto-expand the target so user sees the nested page
      setExpandedIds((prev) => new Set(prev).add(targetId));
    } else {
      // Reorder: place before/after the target within the same parent
      const newParentId = targetItem.page.parentId;

      // Find siblings of target
      const siblings = newParentId
        ? flatItems.filter(
            (f) => f.page.parentId === newParentId && f.depth === targetItem.depth
          )
        : pages;

      const siblingIdx = siblings.findIndex(
        (s) => (s instanceof Object && "page" in s ? s.page.id : (s as Page).id) === targetId
      );
      const newIndex = intent === "after" ? siblingIdx + 1 : siblingIdx;

      onMove(draggedId, newParentId, newIndex);
    }
  }

  function handleDragCancel() {
    setActiveId(null);
    setOverId(null);
  }

  if (loading) {
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
              onPageSelect={onPageSelect}
              onAddChild={onAddChild}
              onDelete={onDelete}
              onRename={onRename}
              onChangeIcon={onChangeIcon}
              isExpanded={expandedIds.has(item.page.id)}
              onToggleExpand={toggleExpanded}
              isDragOver={overId === item.page.id}
              dropIntent={overId === item.page.id ? dropIntent : undefined}
              autoRename={renamingPageId === item.page.id}
              onRenamingDone={onRenamingDone}
            />
          ))}
        </SidebarMenu>

      <DragOverlay dropAnimation={null}>
        {activePage && (
          <div className="flex items-center gap-1.5 rounded-md bg-sidebar-accent px-3 py-1.5 text-sm shadow-md">
            <span className="shrink-0 text-base leading-none">
              {activePage.icon || <FileText className="size-4" />}
            </span>
            <span className="truncate">{activePage.title}</span>
          </div>
        )}
      </DragOverlay>
    </DndContext>
  );
}
