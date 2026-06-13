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

import type { DriveNode, DriveTree, SortOrder } from "@/types/wiki";

import { expandPath } from "@/hooks/use-expanded-folders";
import { useBulkSetSortOrders, useMoveFile } from "@/hooks/use-wiki-mutations";
import { wikiKeys } from "@/hooks/use-wiki-tree";
import { ApiError } from "@/lib/api-client";
import { optimisticMoveFile } from "@/lib/wiki-cache";
import { MIME_FOLDER, MIME_SHORTCUT } from "@/types/wiki";

import { useDragOverStore, type DropIntent } from "./use-drag-over-store";
import { useInFlightMovesStore } from "./use-in-flight-moves";

interface WikiReorder {
  optimisticByContainer: Map<string, string[]>;

  activeNode: DriveNode | null;
  sensors: ReturnType<typeof useSensors>;
  onDragStart: (event: DragStartEvent) => void;
  onDragOver: (event: DragOverEvent) => void;
  onDragEnd: (event: DragEndEvent) => void;
  onDragCancel: () => void;
}

const CONTAINER_PREFIX = "parent:";

const FOLDER_ABOVE_BAND = 0.3;
const FOLDER_BELOW_BAND = 0.7;

function parentIdOf(containerId: string): string | null {
  return containerId.startsWith(CONTAINER_PREFIX)
    ? containerId.slice(CONTAINER_PREFIX.length)
    : null;
}

function sortableContainerId(data: Record<string, unknown> | null | undefined): string | null {
  const sortable = data?.["sortable"] as { containerId?: string } | undefined;
  return sortable?.containerId ?? null;
}

function isFolderTarget(data: Record<string, unknown> | null | undefined): boolean {
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
): SortOrder[] | null {
  const tree = qc.getQueryData<DriveTree>(wikiKeys.driveTree(driveId));
  const isAtDriveRoot = newParentFolderId === driveId;
  const folders = (tree?.folders ?? []).filter((f) =>
    isAtDriveRoot ? f.parentId === undefined : f.parentId === newParentFolderId,
  );
  const filesCache =
    isAtDriveRoot && (tree?.filesComplete ?? true)
      ? tree?.files
      : qc.getQueryData<DriveNode[]>(wikiKeys.folderFiles(newParentFolderId));
  const files = (filesCache ?? []).filter((f) => f.mimeType !== MIME_FOLDER);

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

function lookupFolderName(
  qc: ReturnType<typeof useQueryClient>,
  driveId: string,
  folderId: string,
): string {
  const tree = qc.getQueryData<{ folders: { id: string; name: string }[] }>(
    wikiKeys.driveTree(driveId),
  );
  return tree?.folders.find((f) => f.id === folderId)?.name ?? "folder";
}

function lookupNodeMimeType(
  qc: ReturnType<typeof useQueryClient>,
  driveId: string,
  folderId: string,
): string | null {
  const tree = qc.getQueryData<{ folders: { id: string; mimeType: string }[] }>(
    wikiKeys.driveTree(driveId),
  );
  return tree?.folders.find((f) => f.id === folderId)?.mimeType ?? null;
}

function translateMoveError(err: unknown): string {
  if (err instanceof ApiError && err.status === 403) {
    return "Drive denied this move — you may not have edit permission on the destination, or it's a shortcut.";
  }
  if (err instanceof Error) return err.message;
  return "Move failed";
}

export function useWikiReorder(driveId: string): WikiReorder {
  const qc = useQueryClient();
  const bulkSetSortOrders = useBulkSetSortOrders();
  const moveFile = useMoveFile();
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 5 } }));

  const [optimisticByContainer, setOptimisticByContainer] = useState<Map<string, string[]>>(
    () => new Map(),
  );
  const [activeNode, setActiveNode] = useState<DriveNode | null>(null);

  const findNodeById = (id: string): DriveNode | null => {
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

    let intent: DropIntent | null;
    if (sameContainer) {
      const sortableItems =
        (active.data.current?.["sortable"] as { items?: string[] } | undefined)?.items ?? [];
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

    const overIdStr = String(over.id);
    if (overIdStr.startsWith(CONTAINER_PREFIX)) {
      const newParentFolderId = parentIdOf(overIdStr);
      if (newParentFolderId) handleMoveInto(node, newParentFolderId);
      return;
    }

    const activeSortable = active.data.current?.["sortable"] as
      | { containerId?: string; items?: string[] }
      | undefined;
    const overSortable = over.data.current?.["sortable"] as { containerId?: string } | undefined;
    const activeContainerId = activeSortable?.containerId;
    const overContainerId = overSortable?.containerId;
    if (!activeContainerId || !overContainerId) return;

    const overIsFolder = isFolderTarget(over.data.current);

    if (intent === "into" && overIsFolder) {
      handleMoveInto(node, String(over.id));
      return;
    }

    if (activeContainerId !== overContainerId) {
      const newParentFolderId = parentIdOf(overContainerId);
      const positionHint =
        (intent === "above" || intent === "below") && newParentFolderId
          ? { rowId: String(over.id), intent }
          : null;
      if (newParentFolderId) handleMoveInto(node, newParentFolderId, positionHint);
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
    node: DriveNode | null,
    newParentFolderId: string,
    positionHint: { rowId: string; intent: "above" | "below" } | null = null,
  ): void => {
    if (!node) return;

    if (newParentFolderId === node.id) return;
    if (newParentFolderId === (node.parentId ?? driveId)) return;

    if (lookupNodeMimeType(qc, driveId, newParentFolderId) === MIME_SHORTCUT) {
      toast.error("Can't drop into a shortcut. Open it and drop into the target's folder instead.");
      return;
    }

    const movesStore = useInFlightMovesStore.getState();
    movesStore.start(node.id);

    const rollback = optimisticMoveFile(
      qc,
      node,
      { parentFolderId: newParentFolderId, driveId },
      { parentFolderId: node.parentId, driveId },
    );

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

        if (positionHint) {
          const order = buildDestinationOrder(qc, driveId, newParentFolderId, node, positionHint);
          if (order) bulkSetSortOrders.mutate(order);
        }
      })
      .catch((err: unknown) => {
        rollback();
        toast.error(translateMoveError(err));
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
