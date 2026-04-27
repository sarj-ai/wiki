"use client";

import {
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
  type DragStartEvent,
} from "@dnd-kit/core";
import { arrayMove } from "@dnd-kit/sortable";
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import type { DriveNode } from "@/types/navigator";

import { useBulkSetSortOrders } from "@/hooks/use-navigator-mutations";
import { navigatorKeys } from "@/hooks/use-navigator-tree";

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
  onDragEnd: (event: DragEndEvent) => void;
  onDragCancel: () => void;
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
  };

  const onDragCancel = () => { setActiveNode(null); };

  const onDragEnd = (event: DragEndEvent) => {
    setActiveNode(null);
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
    if (!activeContainerId || !overContainerId || activeContainerId !== overContainerId) return;

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

  return { optimisticByContainer, activeNode, sensors, onDragStart, onDragEnd, onDragCancel };
}
