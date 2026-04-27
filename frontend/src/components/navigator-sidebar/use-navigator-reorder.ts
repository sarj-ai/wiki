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

import type { DriveNode, DriveTree, SortOrder } from "@/types/navigator";

import { expandPath } from "@/hooks/use-expanded-folders";
import { useBulkSetSortOrders, useMoveFile } from "@/hooks/use-navigator-mutations";
import { navigatorKeys } from "@/hooks/use-navigator-tree";
import { optimisticMoveFile } from "@/lib/navigator-cache";
import { MIME_FOLDER } from "@/types/navigator";

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
    const { active, over, delta } = event;
    if (!over) {
      useDragOverStore.getState().setOver({ id: null, containerId: null, intent: null });
      return;
    }
    const overIdStr = String(over.id);
    // Empty-container drops register their droppable id as the
    // ``parent:<id>`` container itself — there's no row, no
    // above/below, and no intent.
    if (overIdStr.startsWith(CONTAINER_PREFIX)) {
      useDragOverStore.getState().setOver({
        id: overIdStr,
        containerId: overIdStr,
        intent: null,
      });
      return;
    }
    const overIsFolder = isFolderTarget(over.data.current);
    const overIsOwnParent = activeNode?.parentId === String(over.id);
    const activeContainer = sortableContainerId(active.data.current);
    const overContainer = sortableContainerId(over.data.current);
    const sameContainer = activeContainer !== null && activeContainer === overContainer;

    // Same-container intent is direction-based with no threshold —
    // entering a different row's rect is enough to flag "swap with
    // that row". The indicator side mirrors the drag direction so
    // the user sees the landing position the moment they enter the
    // row.
    //
    // Cross-container intent uses the dragged element's center
    // relative to the over row — there's no swap semantic to fall
    // back on, so we need a position-on-row signal. ``into`` is
    // suppressed when the over folder is the active's own parent
    // (already inside) so the user can drag a child up onto its
    // parent and have the drop fall through to "move out of this
    // folder" rather than a silent no-op.
    let intent: DropIntent | null;
    if (sameContainer) {
      const sortableItems =
        ((active.data.current?.sortable as { items?: string[] } | undefined)?.items) ?? [];
      const oldIdx = sortableItems.indexOf(String(active.id));
      const overIdx = sortableItems.indexOf(String(over.id));
      intent = oldIdx === overIdx ? null : oldIdx < overIdx ? "below" : "above";
    } else {
      const overEligibleForInto = overIsFolder && !overIsOwnParent;
      intent = computeDropIntent(active, over, delta, overEligibleForInto);
    }
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

    // Empty-container drop: ``EmptyDropZone`` registers its droppable
    // id as ``parent:<destinationId>`` directly so the container's
    // parent id falls right out of ``over.id``. This is how the user
    // gets a destination to land on when the target container has no
    // rows yet (e.g. dragging out into an empty drive root).
    const overIdStr = String(over.id);
    if (overIdStr.startsWith(CONTAINER_PREFIX)) {
      const newParentFolderId = parentIdOf(overIdStr);
      if (newParentFolderId) handleMoveInto(node, newParentFolderId);
      return;
    }

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

    // Cross-container above/below: the dragged row lands at the over
    // row's parent level, then we pin a sort_order so the file
    // actually lands at the targeted slot. Without the pin, the
    // destination's default sort (folders first, then alpha) would
    // always file the moved row below the over row, which is the
    // "section always on top" bug we hit in the recordings.
    if (activeContainerId !== overContainerId) {
      const newParentFolderId = parentIdOf(overContainerId);
      const positionHint =
        (intent === "above" || intent === "below") && newParentFolderId
          ? { rowId: String(over.id), intent }
          : null;
      if (newParentFolderId) handleMoveInto(node, newParentFolderId, positionHint);
      return;
    }

    // Same-container reorder: direction-based swap. ``arrayMove``
    // with the over row's CURRENT index does the right thing in
    // both directions — splice removes the dragged from oldIndex,
    // splice inserts at overIndex. When oldIndex < overIndex (drag
    // down), the over row's index in the post-removal array is
    // overIndex - 1 and we land AT it ("below" the over). When
    // oldIndex > overIndex (drag up), over's index is unchanged and
    // we land AT it ("above" the over). Either way the user sees
    // their dragged item swap into the over's slot.
    //
    // Skipping the threshold-based above/below intent here was the
    // fix for "items at adjacent indices feel impossible to swap" —
    // the math now triggers a swap as soon as the cursor enters the
    // other row, no need to drag past a 50% mark.
    const items = activeSortable.items ?? [];
    const oldIndex = items.indexOf(String(active.id));
    const overIndex = items.indexOf(String(over.id));
    if (oldIndex < 0 || overIndex < 0 || oldIndex === overIndex) return;
    const reordered = arrayMove(items, oldIndex, overIndex);

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

  const handleMoveInto = (
    node: DriveNode | null,
    newParentFolderId: string,
    positionHint: { rowId: string; intent: "above" | "below" } | null = null,
  ): void => {
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
        sourceParentFolderId: node.parentId,
      })
      .then(() => {
        toast.success(`Moved “${node.name}” to “${destinationName}”`);
        // Pin the file at the targeted slot inside the destination
        // by writing sort_orders for the destination's children.
        // Without this, the default folders-first / alpha sort puts
        // the moved file below any folder in the destination, so
        // "drop above the section" silently lands it below.
        if (positionHint) {
          const order = buildDestinationOrder(
            qc,
            driveId,
            newParentFolderId,
            node,
            positionHint,
          );
          if (order) bulkSetSortOrders.mutate(order);
        }
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
 * Project the dragged row's CURRENT visual position into a drop
 * intent relative to the over row.
 *
 * Signal: ``initial.top + delta.y`` — the dragged row's bounding box
 * at drag start, shifted by the cumulative drag offset. That's the
 * pixel-exact location the overlay sits at, grab-point independent.
 *
 * ``active.rect.current.translated`` looks like it should give the
 * same number, but in practice it's frequently null when DragOverlay
 * is in use — dnd-kit doesn't apply the transform to the source row
 * because the overlay handles rendering, so the "translated" field
 * isn't always populated. Falling through to a hard-coded "into"
 * meant every folder hover reported "into" regardless of where the
 * cursor actually was — which is exactly what the user observed.
 *
 * The ``initial`` rect is always set once a drag starts (it's the
 * source row's bounding-client-rect captured on dragStart), and
 * ``event.delta`` is always provided in onDragOver. So this signal
 * is reliable.
 */
function computeDropIntent(
  active: { rect: { current: { initial: { top: number; height: number } | null } } },
  over: { rect: { top: number; height: number } },
  delta: { x: number; y: number },
  overIsFolder: boolean,
): DropIntent {
  const initial = active.rect.current.initial;
  if (!initial) return overIsFolder ? "into" : "below";
  const draggedCenter = initial.top + initial.height / 2 + delta.y;
  const relativeY = (draggedCenter - over.rect.top) / over.rect.height;
  if (overIsFolder) {
    if (relativeY < FOLDER_ABOVE_BAND) return "above";
    if (relativeY > FOLDER_BELOW_BAND) return "below";
    return "into";
  }
  return relativeY < 0.5 ? "above" : "below";
}

/**
 * Build a fresh sort_order list for the destination's children with
 * the moved row pinned above or below the targeted row.
 *
 * Cross-container moves only change the file's parent on the server;
 * order in the new home falls to the default sort (folders first,
 * then alpha). That means a "drop above the folder" intent silently
 * lands the file *below* the folder. Writing sort_orders for the
 * full destination forces the moved file into the user's chosen slot
 * without trying to be clever about fractional indexing.
 */
function buildDestinationOrder(
  qc: ReturnType<typeof useQueryClient>,
  driveId: string,
  newParentFolderId: string,
  movedNode: DriveNode,
  positionHint: { rowId: string; intent: "above" | "below" },
): SortOrder[] | null {
  const tree = qc.getQueryData<DriveTree>(navigatorKeys.driveTree(driveId));
  const isAtDriveRoot = newParentFolderId === driveId;
  const folders = (tree?.folders ?? []).filter((f) =>
    isAtDriveRoot ? f.parentId === null : f.parentId === newParentFolderId,
  );
  const filesCache = qc.getQueryData<DriveNode[]>(
    navigatorKeys.folderFiles(newParentFolderId),
  );
  const files = (filesCache ?? []).filter((f) => f.mimeType !== MIME_FOLDER);

  // Combine folders + files, drop the moved node if the optimistic
  // insert already added it, then default-sort. This mirrors what
  // the render path computes when no sort_orders apply, so the
  // baseline order matches what's currently on screen.
  const baseline = [...folders, ...files]
    .filter((c) => c.id !== movedNode.id)
    .sort((a, b) => {
      const aIsFolder = a.mimeType === MIME_FOLDER;
      const bIsFolder = b.mimeType === MIME_FOLDER;
      if (aIsFolder !== bIsFolder) return aIsFolder ? -1 : 1;
      return a.name.localeCompare(b.name);
    });

  const overIndex = baseline.findIndex((c) => c.id === positionHint.rowId);
  if (overIndex < 0) return null;
  const insertIdx = positionHint.intent === "below" ? overIndex + 1 : overIndex;
  const finalOrder = [...baseline];
  finalOrder.splice(insertIdx, 0, movedNode);
  return finalOrder.map((node, i) => ({ driveId: node.id, sortOrder: i + 1 }));
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
