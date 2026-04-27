"use client";

import {
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
  type DragOverEvent,
  type DragStartEvent,
} from "@dnd-kit/core";
import { arrayMove } from "@dnd-kit/sortable";
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import type { DriveNode } from "@/types/navigator";

import { useBulkSetSortOrders, useMoveFile } from "@/hooks/use-navigator-mutations";
import { navigatorKeys } from "@/hooks/use-navigator-tree";

import { useDragOverStore } from "./use-drag-over-store";

// SortableContext ids in the sidebar follow ``parent:<drive-or-folder-id>``.
// Helper isolates that convention so the rest of this module can talk
// in plain ids and we have one place to flip the encoding if it ever
// changes.
const CONTAINER_PREFIX = "parent:";
function parentIdOf(containerId: string): string | null {
  return containerId.startsWith(CONTAINER_PREFIX)
    ? containerId.slice(CONTAINER_PREFIX.length)
    : null;
}

export interface NavigatorReorder {
  // Map keyed by ``parent:<id>`` carrying the post-drop id order. Read
  // by every TreeNode at every depth so its children render in the
  // dropped order on the same frame the drag ends, bypassing the
  // prop-drilling lag while sortOrders refetches.
  optimisticByContainer: Map<string, string[]>;
  // Node currently under the user's cursor during a drag — populated
  // on dragStart, cleared on dragEnd / dragCancel. Drives the
  // ``DragOverlay`` portal in DriveSection.
  activeNode: DriveNode | null;
  sensors: ReturnType<typeof useSensors>;
  onDragStart: (event: DragStartEvent) => void;
  onDragOver: (event: DragOverEvent) => void;
  onDragEnd: (event: DragEndEvent) => void;
  onDragCancel: () => void;
}

function sortableContainerId(
  data: Record<string, unknown> | null | undefined,
): string | null {
  const sortable = data?.sortable as { containerId?: string } | undefined;
  return sortable?.containerId ?? null;
}

/**
 * Drag-and-drop reorder state machine for a single drive's
 * ``DndContext``. Owns the optimistic order map, the active-node
 * lookup, and the bulk-sortOrders mutation lifecycle so DriveSection
 * stays a thin composition over rendering primitives.
 *
 * The sortableContext id convention is ``parent:<drive-or-folder-id>``
 * — both drive root and every subfolder share the same shape so a
 * single state-machine instance services every depth.
 */
export function useNavigatorReorder(driveId: string): NavigatorReorder {
  const qc = useQueryClient();
  const bulkSetSortOrders = useBulkSetSortOrders();
  const moveFile = useMoveFile();
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
  );

  const [optimisticByContainer, setOptimisticByContainer] = useState<Map<string, string[]>>(
    () => new Map(),
  );
  const [activeNode, setActiveNode] = useState<DriveNode | null>(null);

  // Looks up a dragged row's DriveNode across every cache the sidebar
  // populates: the drive tree (for folders) and every folder-files
  // listing the user has expanded (for files). useQueryClient is the
  // honest tool here — there's no single hook that aggregates
  // arbitrary sub-folder file lists, and copying the data into a
  // Context just to re-derive it on every render would cost more.
  const findNodeById = (id: string): DriveNode | null => {
    const tree = qc.getQueryData<{ folders: DriveNode[] }>(navigatorKeys.driveTree(driveId));
    const folder = tree?.folders.find((n) => n.id === id);
    if (folder) return folder;
    const folderFilesCaches = qc.getQueriesData<DriveNode[]>({
      queryKey: ["navigator", "folder-files"],
    });
    for (const [, nodes] of folderFilesCaches) {
      const hit = nodes?.find((n) => n.id === id);
      if (hit) return hit;
    }
    return null;
  };

  const onDragStart = (event: DragStartEvent) => {
    setActiveNode(findNodeById(String(event.active.id)));
    // Publish the drag's identity so cross-container drop targets can
    // self-highlight and collapsed folders can detect that they're
    // being hovered for auto-expand. Reset on cancel/end.
    useDragOverStore.getState().setActive({
      id: String(event.active.id),
      containerId: sortableContainerId(event.active.data.current),
    });
  };

  const onDragOver = (event: DragOverEvent) => {
    const { over } = event;
    if (!over) {
      useDragOverStore.getState().setOver({ id: null, containerId: null });
      return;
    }
    useDragOverStore.getState().setOver({
      id: String(over.id),
      containerId: sortableContainerId(over.data.current),
    });
  };

  const onDragCancel = () => {
    setActiveNode(null);
    useDragOverStore.getState().reset();
  };

  const onDragEnd = (event: DragEndEvent) => {
    setActiveNode(null);
    useDragOverStore.getState().reset();
    const { active, over } = event;
    if (!over || active.id === over.id) return;

    const activeSortable = active.data.current?.sortable as
      | { containerId?: string; items?: string[] }
      | undefined;
    const overSortable = over.data.current?.sortable as
      | { containerId?: string }
      | undefined;
    const activeContainerId = activeSortable?.containerId;
    const overContainerId = overSortable?.containerId;
    if (!activeContainerId || !overContainerId) return;

    // Cross-container drop: dragging out of one folder/drive root and
    // into another means the user wants to *move* the row, not reorder
    // it. Resolve the destination's parent id from the over container
    // id and dispatch the move mutation. The bulk-sortOrders path
    // below only fires for same-container drops (reorder).
    if (activeContainerId !== overContainerId) {
      handleCrossContainerDrop(String(active.id), overContainerId);
      return;
    }

    const items = activeSortable.items ?? [];
    const oldIndex = items.indexOf(String(active.id));
    const newIndex = items.indexOf(String(over.id));
    if (oldIndex < 0 || newIndex < 0) return;

    const reordered = arrayMove(items, oldIndex, newIndex);

    // Optimistic per-container reorder. Works for BOTH drive root and
    // every subfolder — readers (TreeNode) consult this map before
    // falling back to sortOrderMap-based sorting, so rows land in the
    // new order on the same frame the drop fires.
    setOptimisticByContainer((prev) => {
      const next = new Map(prev);
      next.set(activeContainerId, reordered);
      return next;
    });

    const updates = reordered.map((id, i) => ({ driveId: id, sortOrder: i + 1 }));
    void bulkSetSortOrders
      .mutateAsync(updates)
      .catch((err: unknown) => {
        const message = err instanceof Error ? err.message : "Reorder failed";
        toast.error(message);
      })
      .finally(() => {
        setOptimisticByContainer((prev) => {
          if (!prev.has(activeContainerId)) return prev;
          const next = new Map(prev);
          next.delete(activeContainerId);
          return next;
        });
      });
  };

  const handleCrossContainerDrop = (fileId: string, overContainerId: string): void => {
    const newParentFolderId = parentIdOf(overContainerId);
    if (!newParentFolderId) return;
    // Drive's API rejects moving a folder into itself or its own
    // descendants — fail fast on the same-id case so the toast is
    // immediate. The deeper "into descendant" check still falls to
    // Drive (it returns 400) since detecting it client-side requires
    // walking the tree on every drop.
    if (newParentFolderId === fileId) return;
    void moveFile
      .mutateAsync({
        fileId,
        newParentFolderId,
        sourceDriveId: driveId,
      })
      .catch((err: unknown) => {
        const message = err instanceof Error ? err.message : "Move failed";
        toast.error(message);
      });
  };

  return {
    optimisticByContainer,
    activeNode,
    sensors,
    onDragStart,
    onDragOver,
    onDragEnd,
    onDragCancel,
  };
}
