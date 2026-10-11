"use client";

import {
  type DragEndEvent,
  type DragOverEvent,
  type DragStartEvent,
  PointerSensor,
  useSensor,
  useSensors,
} from "@dnd-kit/core";
import { arrayMove } from "@dnd-kit/sortable";
import { useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { toast } from "sonner";

import { expandPath } from "@/hooks/use-expanded-folders";
import { useBulkSetSortOrders, useMoveFile } from "@/hooks/use-wiki-mutations";
import { wikiKeys } from "@/hooks/use-wiki-tree";
import { ApiError } from "@/lib/api-error";
import { useBrowserUserId } from "@/lib/browser-user-context";
import { buildDestinationSortOrders } from "@/lib/build-destination-sort-orders";
import { validateCrossDriveMove } from "@/lib/validate-cross-drive-move";
import { optimisticMoveFile } from "@/lib/wiki-cache";
import { EMPTY_NODES, EMPTY_SORT_ORDERS } from "@/lib/wiki-indexes";
import type { DriveNode, DriveTree, SortOrder } from "@/types/wiki";
import { MIME_SHORTCUT } from "@/types/wiki";

import { dragScopeData, sortableContainerId, sortableData } from "./drag-data";
import { type DropIntent, useDragOverStore } from "./use-drag-over-store";
import { useInFlightMovesStore } from "./use-in-flight-moves";

interface WikiReorder {
  activeNode: DriveNode | null;

  onDragCancel: () => void;
  onDragEnd: (event: DragEndEvent) => void;
  onDragOver: (event: DragOverEvent) => void;
  onDragStart: (event: DragStartEvent) => void;
  optimisticByContainer: Map<string, string[]>;
  sensors: ReturnType<typeof useSensors>;
}

interface ActiveDrag {
  driveId: string;
  node: DriveNode;
  parentFolderId: string | undefined;
}

const CONTAINER_PREFIX = "parent:";

const FOLDER_ABOVE_BAND = 0.3;
const FOLDER_BELOW_BAND = 0.7;

export function useWikiReorder(): WikiReorder {
  const qc = useQueryClient();
  const userId = useBrowserUserId();
  const bulkSetSortOrders = useBulkSetSortOrders();
  const moveFile = useMoveFile();
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 5 } }));

  const [optimisticByContainer, setOptimisticByContainer] = useState<Map<string, string[]>>(
    () => new Map(),
  );
  const [activeNode, setActiveNode] = useState<DriveNode | null>(null);
  const activeDrag = useRef<ActiveDrag | null>(null);

  const findNodeById = (driveId: string, id: string): DriveNode | null => {
    const tree = qc.getQueryData<DriveTree>(wikiKeys.driveTree(driveId));
    const treeHit = tree?.folders.find((n) => n.id === id) ?? tree?.files.find((n) => n.id === id);
    if (treeHit) return treeHit;
    const folderFilesCaches = qc.getQueriesData<DriveNode[]>({
      queryKey: wikiKeys.folderFilesPrefix,
    });
    for (const [, nodes] of folderFilesCaches) {
      const hit = nodes?.find((n) => n.id === id);
      if (hit) return hit;
    }
    return null;
  };

  const onDragStart = (event: DragStartEvent) => {
    const scope = dragScopeData(event.active.data.current);
    if (!scope) return;
    const node = findNodeById(scope.driveId, String(event.active.id));
    if (!node) return;
    activeDrag.current = {
      driveId: scope.driveId,
      node,
      parentFolderId: scope.parentFolderId,
    };
    setActiveNode(node);
    useDragOverStore.getState().setActive({
      containerId: sortableContainerId(event.active.data.current),
      id: String(event.active.id),
    });
  };

  const onDragOver = (event: DragOverEvent) => {
    const { active, delta, over } = event;
    const dragged = activeDrag.current;
    if (!dragged) return;
    if (!over) {
      useDragOverStore.getState().setOver({ containerId: null, id: null, intent: null });
      return;
    }
    const overIdStr = String(over.id);

    if (overIdStr.startsWith(CONTAINER_PREFIX)) {
      useDragOverStore.getState().setOver({
        containerId: overIdStr,
        id: overIdStr,
        intent: null,
      });
      return;
    }
    const overIsFolder = isFolderTarget(over.data.current);
    const overIsOwnParent = dragged.parentFolderId === String(over.id);
    const activeContainer = sortableContainerId(active.data.current);
    const overContainer = sortableContainerId(over.data.current);
    const sameContainer = activeContainer !== null && activeContainer === overContainer;

    let intent: DropIntent | null;
    if (sameContainer) {
      const sortableItems = sortableData(active.data.current)?.items ?? [];
      const oldIdx = sortableItems.indexOf(String(active.id));
      const overIdx = sortableItems.indexOf(String(over.id));
      intent = oldIdx === overIdx ? null : oldIdx < overIdx ? "below" : "above";
    } else {
      const overEligibleForInto = overIsFolder && !overIsOwnParent;
      intent = computeDropIntent(active, over, delta, overEligibleForInto);
    }
    useDragOverStore.getState().setOver({
      containerId: overContainer,
      id: String(over.id),
      intent,
    });
  };

  const onDragCancel = () => {
    activeDrag.current = null;
    setActiveNode(null);
    useDragOverStore.getState().reset();
  };

  const onDragEnd = (event: DragEndEvent) => {
    const dragged = activeDrag.current;
    const intent = useDragOverStore.getState().intent;
    activeDrag.current = null;
    setActiveNode(null);
    useDragOverStore.getState().reset();

    const { active, over } = event;
    if (!dragged || !over || active.id === over.id) return;

    const destinationScope = dragScopeData(over.data.current);
    if (!destinationScope) return;

    const overIdStr = String(over.id);
    if (overIdStr.startsWith(CONTAINER_PREFIX)) {
      const newParentFolderId = parentIdOf(overIdStr);
      if (newParentFolderId) {
        handleMoveInto(dragged, destinationScope.driveId, newParentFolderId);
      }
      return;
    }

    const activeSortable = sortableData(active.data.current);
    const overSortable = sortableData(over.data.current);
    const activeContainerId = activeSortable?.containerId;
    const overContainerId = overSortable?.containerId;
    if (!activeContainerId || !overContainerId) return;

    const overIsFolder = isFolderTarget(over.data.current);

    if (intent === "into" && overIsFolder) {
      handleMoveInto(dragged, destinationScope.driveId, String(over.id));
      return;
    }

    if (activeContainerId !== overContainerId) {
      const newParentFolderId = parentIdOf(overContainerId);
      const positionHint =
        (intent === "above" || intent === "below") && newParentFolderId
          ? { intent, rowId: String(over.id) }
          : null;
      if (newParentFolderId) {
        handleMoveInto(dragged, destinationScope.driveId, newParentFolderId, positionHint);
      }
      return;
    }

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
    dragged: ActiveDrag,
    destinationDriveId: string,
    newParentFolderId: string,
    positionHint: { rowId: string; intent: "above" | "below" } | null = null,
  ): void => {
    const { driveId: sourceDriveId, node, parentFolderId: sourceParentFolderId } = dragged;

    if (newParentFolderId === node.id) return;
    if (
      destinationDriveId === sourceDriveId &&
      newParentFolderId === (sourceParentFolderId ?? sourceDriveId)
    ) {
      return;
    }

    const crossDriveError = validateCrossDriveMove({
      destinationDriveId,
      mimeType: node.mimeType,
      sourceDriveId,
    });
    if (crossDriveError !== null) {
      toast.error(crossDriveError);
      return;
    }

    const destinationNode = lookupFolder(qc, destinationDriveId, newParentFolderId);
    if (destinationNode?.mimeType === MIME_SHORTCUT) {
      toast.error("Can't drop into a shortcut. Open it and drop into the target's folder instead.");
      return;
    }

    const movesStore = useInFlightMovesStore.getState();
    movesStore.start(node.id);

    const rollback = optimisticMoveFile(
      qc,
      node,
      { driveId: destinationDriveId, parentFolderId: newParentFolderId },
      { driveId: sourceDriveId, parentFolderId: sourceParentFolderId },
    );

    if (newParentFolderId !== destinationDriveId) {
      expandPath(userId, destinationDriveId, [newParentFolderId]);
    }

    const destinationName = destinationNode?.name ?? "folder";

    void (async () => {
      await moveFile.mutateAsync({
        fileId: node.id,
        newParentFolderId,
        sourceDriveId,
        sourceParentFolderId,
      });
      toast.success(`Moved “${node.name}” to “${destinationName}”`);

      if (positionHint) {
        const order = buildDestinationOrder(
          qc,
          destinationDriveId,
          newParentFolderId,
          node,
          positionHint,
        );
        if (order) bulkSetSortOrders.mutate(order);
      }
    })()
      .catch((err: unknown) => {
        rollback();
        toast.error(translateMoveError(err));
      })
      .finally(() => {
        movesStore.end(node.id);
      });
  };

  return {
    activeNode,
    onDragCancel,
    onDragEnd,
    onDragOver,
    onDragStart,
    optimisticByContainer,
    sensors,
  };
}

function parentIdOf(containerId: string): null | string {
  return containerId.startsWith(CONTAINER_PREFIX)
    ? containerId.slice(CONTAINER_PREFIX.length)
    : null;
}

function isFolderTarget(data: null | Record<string, unknown> | undefined): boolean {
  return data?.["isFolder"] === true;
}

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

function buildDestinationOrder(
  qc: ReturnType<typeof useQueryClient>,
  driveId: string,
  newParentFolderId: string,
  movedNode: DriveNode,
  positionHint: { rowId: string; intent: "above" | "below" },
): null | SortOrder[] {
  const tree = qc.getQueryData<DriveTree>(wikiKeys.driveTree(driveId));
  return buildDestinationSortOrders({
    driveId,
    folderFiles:
      qc.getQueryData<DriveNode[]>(wikiKeys.folderFiles(newParentFolderId)) ?? EMPTY_NODES,
    folderId: newParentFolderId,
    movedNode,
    positionHint,
    sortOrders: qc.getQueryData<SortOrder[]>(wikiKeys.sortOrders) ?? EMPTY_SORT_ORDERS,
    tree,
  });
}

function lookupFolder(
  qc: ReturnType<typeof useQueryClient>,
  driveId: string,
  folderId: string,
): DriveNode | undefined {
  const tree = qc.getQueryData<DriveTree>(wikiKeys.driveTree(driveId));
  return tree?.folders.find((folder) => folder.id === folderId);
}

function translateMoveError(err: unknown): string {
  if (err instanceof ApiError && err.status === 403) {
    return "Drive denied this move — you may not have edit permission on the destination, or it's a shortcut.";
  }
  if (err instanceof Error) return err.message;
  return "Move failed";
}
