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
import { useRef, useState } from "react";
import { toast } from "sonner";

import type { DriveNode } from "@/types/navigator";

import { expandPath } from "@/hooks/use-expanded-folders";
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
  // Pointer Y at drag start, captured from the activator event. Used
  // by ``computeDropIntent`` together with ``event.delta.y`` to
  // recover the live cursor Y in onDragOver — the dragged element's
  // translated rect's center is *only* equal to the cursor when the
  // user grabbed at the exact middle of the row, which is rare. The
  // delta-from-start path is accurate to within a pixel regardless of
  // grab point.
  const initialPointerY = useRef<number | null>(null);

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
    initialPointerY.current = pointerYFromEvent(event.activatorEvent);
    useDragOverStore.getState().setActive({
      id: String(event.active.id),
      containerId: sortableContainerId(event.active.data.current),
    });
  };

  const onDragOver = (event: DragOverEvent) => {
    const { over } = event;
    if (!over) {
      useDragOverStore.getState().setOver({ id: null, containerId: null, intent: null });
      return;
    }
    const startY = initialPointerY.current;
    const cursorY = startY === null ? null : startY + event.delta.y;
    const intent = computeDropIntent(cursorY, over, isFolderTarget(over.data.current));
    useDragOverStore.getState().setOver({
      id: String(over.id),
      containerId: sortableContainerId(over.data.current),
      intent,
    });
  };

  const onDragCancel = () => {
    setActiveNode(null);
    initialPointerY.current = null;
    useDragOverStore.getState().reset();
  };

  const onDragEnd = (event: DragEndEvent) => {
    const node = activeNode;
    const intent = useDragOverStore.getState().intent;
    setActiveNode(null);
    initialPointerY.current = null;
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

    // Same container: reorder above/below the over row. arrayMove's
    // signature is "place dragged AT this index in the result", so we
    // adjust by +1 for "below" intent and account for the dragged
    // item being removed from earlier in the array (which shifts the
    // over row's index down by 1).
    const items = activeSortable.items ?? [];
    const oldIndex = items.indexOf(String(active.id));
    const overIndex = items.indexOf(String(over.id));
    if (oldIndex < 0 || overIndex < 0) return;
    const newIndex = resolveReorderIndex(oldIndex, overIndex, intent);
    if (newIndex === oldIndex) return;
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

    // Auto-expand the destination folder so the user actually SEES
    // the dropped row land there — without this, dropping into a
    // collapsed folder makes the file disappear from the source with
    // no visible landing. Drive root is always visible so we skip it.
    // The drive id is also pinned open in case it was collapsed.
    if (newParentFolderId !== driveId) {
      expandPath(driveId, [newParentFolderId]);
    }

    const destinationName = lookupFolderName(qc, driveId, newParentFolderId);

    void moveFile
      .mutateAsync({
        fileId: node.id,
        newParentFolderId,
        sourceDriveId: driveId,
      })
      .then(() => {
        toast.success(`Moved “${node.name}” to “${destinationName}”`);
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
 * folder rows have no into option and split in half.
 *
 * ``cursorY`` is computed in the caller from the activator pointer
 * Y plus the cumulative drag delta — that's accurate to a pixel
 * regardless of where on the row the user grabbed. Falling back to
 * "into"/"below" only when the activator wasn't a pointer event
 * (keyboard sensor, etc.).
 */
function computeDropIntent(
  cursorY: number | null,
  over: { rect: { top: number; height: number } },
  overIsFolder: boolean,
): DropIntent {
  if (cursorY === null) return overIsFolder ? "into" : "below";
  const relativeY = (cursorY - over.rect.top) / over.rect.height;
  if (overIsFolder) {
    if (relativeY < FOLDER_ABOVE_BAND) return "above";
    if (relativeY > FOLDER_BELOW_BAND) return "below";
    return "into";
  }
  return relativeY < 0.5 ? "above" : "below";
}

/**
 * Pull a clientY out of dnd-kit's activator event. Pointer/Mouse/Touch
 * events all expose clientY; the keyboard sensor's activator is a
 * KeyboardEvent which doesn't, in which case we return null and
 * computeDropIntent falls back to a sensible default.
 */
function pointerYFromEvent(event: Event): number | null {
  if (event instanceof PointerEvent || event instanceof MouseEvent) {
    return event.clientY;
  }
  if (event instanceof TouchEvent) {
    const touch = event.touches[0] ?? event.changedTouches[0];
    return touch?.clientY ?? null;
  }
  return null;
}

/**
 * Compute the destination index for a same-container reorder. The
 * over row's "current" index shifts by 1 once the dragged item is
 * removed from earlier in the array, and "below" intent adds another
 * 1 to land after the over row. Returns ``oldIndex`` when the result
 * would be a no-op so the caller can short-circuit the mutation.
 */
function resolveReorderIndex(
  oldIndex: number,
  overIndex: number,
  intent: DropIntent | null,
): number {
  // The dragged row's removal shifts every later index down by 1;
  // adjust the over row's index so we treat it as a position in the
  // post-removal array.
  const overInRemoved = oldIndex < overIndex ? overIndex - 1 : overIndex;
  if (intent === "below") return overInRemoved + 1;
  // Default and "above": land before the over row.
  return overInRemoved;
}

/**
 * Look up a folder's display name from the cached drive tree so the
 * post-move toast can name the destination instead of falling back to
 * "folder". The drive tree is always cached for any expanded drive
 * (which the destination must be, since the user just dropped on a
 * row inside it), so this lookup is essentially free.
 */
function lookupFolderName(
  qc: ReturnType<typeof useQueryClient>,
  driveId: string,
  folderId: string,
): string {
  const tree = qc.getQueryData<{ folders: { id: string; name: string }[] }>(
    navigatorKeys.driveTree(driveId),
  );
  return tree?.folders.find((f) => f.id === folderId)?.name ?? "folder";
}
