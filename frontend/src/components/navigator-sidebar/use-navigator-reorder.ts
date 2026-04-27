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
import { optimisticMoveFile } from "@/lib/navigator-cache";

import { useDragOverStore, type DropIntent } from "./use-drag-over-store";
import { useInFlightMovesStore } from "./use-in-flight-moves";

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

// Folder rows are split into thirds so the user can drop above, into,
// or below; non-folder rows split in half (no into option). The
// thresholds are expressed as fractions of the row's height.
const FOLDER_ABOVE_BAND = 0.3;
const FOLDER_BELOW_BAND = 0.7;

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

function isFolderTarget(data: Record<string, unknown> | null | undefined): boolean {
  return data?.isFolder === true;
}

/**
 * Drag-and-drop reorder state machine for a single drive's
 * ``DndContext``. Owns the optimistic order map, the active-node
 * lookup, the bulk-sortOrders + move mutation lifecycles, and the
 * drop-intent computation.
 *
 * Drop intent: cursor Y position on the over row's bounding box
 * decides whether the dragged row lands above, below, or *into* the
 * over row. Folder rows get a middle "into" band so the user can
 * drop a file inside them; non-folder rows split in half.
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
    useDragOverStore.getState().setActive({
      id: String(event.active.id),
      containerId: sortableContainerId(event.active.data.current),
    });
  };

  const onDragOver = (event: DragOverEvent) => {
    const { over, active } = event;
    if (!over) {
      useDragOverStore.getState().setOver({ id: null, containerId: null, intent: null });
      return;
    }
    const intent = computeDropIntent(active, over, isFolderTarget(over.data.current));
    useDragOverStore.getState().setOver({
      id: String(over.id),
      containerId: sortableContainerId(over.data.current),
      intent,
    });
  };

  const onDragCancel = () => {
    setActiveNode(null);
    useDragOverStore.getState().reset();
  };

  const onDragEnd = (event: DragEndEvent) => {
    const node = activeNode;
    const intent = useDragOverStore.getState().intent;
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

    const overIsFolder = isFolderTarget(over.data.current);

    // Drop INTO a folder: only valid when the over row is a folder
    // and the cursor sat in the middle band. Move regardless of
    // container; the new parent is the over row itself.
    if (intent === "into" && overIsFolder) {
      handleMoveInto(node, String(over.id));
      return;
    }

    // Cross-container above/below or fallback: the dragged row lands
    // at the over row's parent level. Move into that parent. (We can't
    // express "between two specific siblings" through Drive's API, so
    // both above/below collapse to "into the parent".)
    if (activeContainerId !== overContainerId) {
      const newParentFolderId = parentIdOf(overContainerId);
      if (newParentFolderId) handleMoveInto(node, newParentFolderId);
      return;
    }

    // Same container: reorder above/below the over row.
    const items = activeSortable.items ?? [];
    const oldIndex = items.indexOf(String(active.id));
    const newIndex = items.indexOf(String(over.id));
    if (oldIndex < 0 || newIndex < 0) return;
    const reordered = arrayMove(items, oldIndex, newIndex);

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

  const handleMoveInto = (node: DriveNode | null, newParentFolderId: string): void => {
    if (!node) return;
    // Drive's API rejects moving a folder into itself. Deeper
    // "into descendant" cycles fall through to Drive's 400 — we'd
    // need to walk the tree to detect them client-side and the
    // server's response is fast enough to surface as a toast.
    if (newParentFolderId === node.id) return;
    if (newParentFolderId === (node.parentId ?? driveId)) return; // no-op move

    const movesStore = useInFlightMovesStore.getState();
    movesStore.start(node.id);

    // Optimistic insert: pull the node out of its source cache and
    // splice it into the destination so the row appears at the drop
    // site on the same frame. Rollback if the mutation fails.
    const rollback = optimisticMoveFile(
      qc,
      node,
      { parentFolderId: newParentFolderId, driveId },
      { parentFolderId: node.parentId, driveId },
    );

    void moveFile
      .mutateAsync({
        fileId: node.id,
        newParentFolderId,
        sourceDriveId: driveId,
      })
      .catch((err: unknown) => {
        rollback();
        const message = err instanceof Error ? err.message : "Move failed";
        toast.error(message);
      })
      .finally(() => {
        movesStore.end(node.id);
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

/**
 * Project the cursor's Y position on the over row's bounding box
 * into a drop intent. Folder rows get a middle "into" band; non-
 * folder rows have no into option and split in half. ``active``'s
 * translated rect is the closest signal to "where the cursor is"
 * dnd-kit exposes through the over event.
 */
function computeDropIntent(
  active: { rect: { current: { translated: { top: number; height: number } | null } } },
  over: { rect: { top: number; height: number } },
  overIsFolder: boolean,
): DropIntent {
  const activeRect = active.rect.current.translated;
  if (!activeRect) return overIsFolder ? "into" : "below";
  const activeCenterY = activeRect.top + activeRect.height / 2;
  const relativeY = (activeCenterY - over.rect.top) / over.rect.height;
  if (overIsFolder) {
    if (relativeY < FOLDER_ABOVE_BAND) return "above";
    if (relativeY > FOLDER_BELOW_BAND) return "below";
    return "into";
  }
  return relativeY < 0.5 ? "above" : "below";
}
