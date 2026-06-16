"use client";

import {
  closestCorners,
  DndContext,
  DragOverlay,
  pointerWithin,
  rectIntersection,
  type CollisionDetection,
} from "@dnd-kit/core";
import { SortableContext, verticalListSortingStrategy } from "@dnd-kit/sortable";
import { ChevronRight, HardDrive, Loader2, RefreshCw } from "lucide-react";
import { useMemo } from "react";
import { toast } from "sonner";

import type { DriveNode, DriveSummary, FolderIcon, SortOrder } from "@/types/wiki";

import { Button } from "@/components/ui/button";
import {
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarMenu,
  SidebarMenuItem,
} from "@/components/ui/sidebar";
import { Skeleton } from "@/components/ui/skeleton";
import { useExpandedDrive } from "@/hooks/use-expanded-folders";
import { useRefreshDrive } from "@/hooks/use-wiki-mutations";
import { useFolderFiles } from "@/hooks/use-wiki-tree";
import { cn } from "@/lib/utils";
import {
  applyOptimisticOrder,
  compareNodeOrder,
  isFolderLikeNode,
  type KindedNode,
} from "@/lib/wiki-tree";
import { MIME_DOC, MIME_FOLDER } from "@/types/wiki";

import { CreateFileMenu } from "./create-file-menu";
import { DriveSectionProvider, type DriveSectionValue } from "./drive-context";
import { EmptyDropZone } from "./empty-droppable";
import { FileRow } from "./file-row";
import { SortableItem } from "./sortable-item";
import { TreeNode } from "./tree-node";
import { useIsCrossContainerDropTarget } from "./use-drag-over-store";
import { useDriveTreeIndex } from "./use-drive-tree-index";
import { treeRowProps } from "./use-sidebar-keyboard-nav";
import { useWikiReorder } from "./use-wiki-reorder";

interface DriveSectionProps {
  drive: DriveSummary;
  activeDriveId: string | null;
  showNonDocFiles: boolean;
  folderIcons: FolderIcon[];
  sortOrders: SortOrder[];
}

interface BuildRootItemsArgs {
  childrenByParent: Map<string, DriveNode[]>;
  rootFiles: DriveNode[];
  sortOrderMap: Map<string, number>;
  showNonDocFiles: boolean;
}

const cursorFirstCollision: CollisionDetection = (args) => {
  const pointer = pointerWithin(args);
  if (pointer.length > 0) return pointer;
  const intersect = rectIntersection(args);
  if (intersect.length > 0) return intersect;
  return closestCorners(args);
};

function buildRootItems({
  childrenByParent,
  rootFiles,
  sortOrderMap,
  showNonDocFiles,
}: BuildRootItemsArgs): KindedNode[] {
  const folders: KindedNode[] = (childrenByParent.get("__root__") ?? []).map((node) => ({
    type: "folder",
    node,
  }));

  const rawFiles = rootFiles.filter((n) => !isFolderLikeNode(n));
  const visibleFiles = showNonDocFiles ? rawFiles : rawFiles.filter((n) => n.mimeType === MIME_DOC);
  const files: KindedNode[] = visibleFiles.map((node) => ({ type: "file", node }));
  return [...folders, ...files].sort((a, b) => compareNodeOrder(a, b, sortOrderMap));
}

function DriveTreeSkeleton() {
  return (
    <SidebarMenuItem className="flex flex-col gap-1.5 px-2 py-1">
      <div className="flex items-center gap-2 pl-1">
        <Skeleton className="size-3.5 rounded-sm" />
        <Skeleton className="h-3.5 w-32 rounded" />
      </div>
      <div className="flex items-center gap-2 pl-5">
        <Skeleton className="size-3.5 rounded-sm" />
        <Skeleton className="h-3.5 w-40 rounded" />
      </div>
      <div className="flex items-center gap-2 pl-5">
        <Skeleton className="size-3.5 rounded-sm" />
        <Skeleton className="h-3.5 w-24 rounded" />
      </div>
      <div className="flex items-center gap-2 pl-1">
        <Skeleton className="size-3.5 rounded-sm" />
        <Skeleton className="h-3.5 w-36 rounded" />
      </div>
    </SidebarMenuItem>
  );
}

export function DriveSection({
  drive,
  activeDriveId,
  showNonDocFiles,
  folderIcons,
  sortOrders,
}: DriveSectionProps) {
  const [expanded, setExpanded] = useExpandedDrive(drive.id);
  const {
    childrenByParent,
    filesByParent,
    filesComplete,
    iconOverrides,
    docIconOverrides,
    sortOrderMap,
    treeQuery,
  } = useDriveTreeIndex(drive.id, folderIcons, sortOrders);
  const lazyRootFiles = useFolderFiles(drive.id, expanded && !filesComplete);
  const reorder = useWikiReorder(drive.id);
  const refreshDrive = useRefreshDrive();

  const handleRefresh = (e: React.MouseEvent) => {
    e.stopPropagation();
    refreshDrive.mutate(drive.id, {
      onError: (err) => {
        const message = err instanceof Error ? err.message : "Refresh failed";
        toast.error(message);
      },
    });
  };

  const rootContainerId = `parent:${drive.id}`;
  const isDropTarget = useIsCrossContainerDropTarget(rootContainerId);
  const rootItems = buildRootItems({
    childrenByParent,
    rootFiles: filesComplete ? (filesByParent.get("__root__") ?? []) : (lazyRootFiles.data ?? []),
    sortOrderMap,
    showNonDocFiles,
  });
  const optimisticOrder = reorder.optimisticByContainer.get(rootContainerId) ?? null;
  const displayItems = applyOptimisticOrder(rootItems, optimisticOrder);
  const displayItemIds = displayItems.map((item) => item.node.id);

  const contextValue: DriveSectionValue = useMemo(
    () => ({
      driveId: drive.id,
      activeDriveId,
      showNonDocFiles,
      childrenByParent,
      filesByParent,
      treeFilesComplete: filesComplete,
      iconOverrides,
      docIconOverrides,
      sortOrderMap,
      optimisticByContainer: reorder.optimisticByContainer,
    }),
    [
      drive.id,
      activeDriveId,
      showNonDocFiles,
      childrenByParent,
      filesByParent,
      filesComplete,
      iconOverrides,
      docIconOverrides,
      sortOrderMap,
      reorder.optimisticByContainer,
    ],
  );

  return (
    <DriveSectionProvider value={contextValue}>
      <SidebarGroup>
        <div className="group/driveheader relative">
          <SidebarGroupLabel
            asChild
            className={cn(
              "rounded-md transition-[background-color,box-shadow] duration-150",

              isDropTarget && "ring-primary/60 bg-primary/10 ring-1",
            )}
          >
            <button
              type="button"
              {...treeRowProps(drive.id, 1, "drive")}
              onClick={() => {
                setExpanded(!expanded);
              }}
              className="flex w-full items-center gap-1 pr-7"
              aria-expanded={expanded}
            >
              <ChevronRight
                className={cn("size-3 transition-transform", expanded && "rotate-90")}
              />
              <HardDrive className="size-3.5" />
              <span className="truncate">{drive.name}</span>
            </button>
          </SidebarGroupLabel>
          <div className="absolute top-1 right-1 flex items-center gap-0.5 opacity-0 group-hover/driveheader:opacity-100 focus-within:opacity-100">
            <Button
              type="button"
              variant="ghost"
              size="icon-xs"
              aria-label="Refresh drive"
              title="Refresh drive — pulls the latest from Drive instead of waiting for the cache to expire"
              onClick={handleRefresh}
              disabled={refreshDrive.isPending}
              className="size-6 shrink-0"
            >
              {refreshDrive.isPending ? (
                <Loader2 className="size-3.5 animate-spin" />
              ) : (
                <RefreshCw className="size-3.5" />
              )}
            </Button>
            <CreateFileMenu parentFolderId={drive.id} />
          </div>
        </div>
        {expanded && (
          <DndContext
            sensors={reorder.sensors}
            collisionDetection={cursorFirstCollision}
            onDragStart={reorder.onDragStart}
            onDragOver={reorder.onDragOver}
            onDragEnd={reorder.onDragEnd}
            onDragCancel={reorder.onDragCancel}
          >
            <SidebarGroupContent>
              <SidebarMenu>
                {(treeQuery.isPending || (!treeQuery.data && treeQuery.isFetching)) && (
                  <DriveTreeSkeleton />
                )}
                {treeQuery.isError && !treeQuery.isFetching && !treeQuery.data && (
                  <SidebarMenuItem>
                    <span className="text-destructive px-2 py-1 text-xs">Failed to load drive</span>
                  </SidebarMenuItem>
                )}
                {!treeQuery.isLoading && (
                  <SortableContext
                    id={rootContainerId}
                    items={displayItemIds}
                    strategy={verticalListSortingStrategy}
                  >
                    {displayItems.map(({ type, node }) => (
                      <SortableItem
                        key={node.id}
                        id={node.id}
                        variant="item"
                        data={{ isFolder: type === "folder" }}
                      >
                        {type === "folder" ? (
                          <TreeNode node={node} parentFolderId={undefined} depth={0} />
                        ) : (
                          <FileRow
                            node={node}
                            driveId={drive.id}
                            parentFolderId={undefined}
                            activeDriveId={activeDriveId}
                            iconOverride={docIconOverrides.get(node.id)}
                            depth={0}
                          />
                        )}
                      </SortableItem>
                    ))}
                  </SortableContext>
                )}
                {!treeQuery.isLoading && rootItems.length === 0 && (
                  <EmptyDropZone parentId={drive.id} variant="item" label="Empty drive" />
                )}
              </SidebarMenu>
            </SidebarGroupContent>

            <DragOverlay dropAnimation={null}>
              {reorder.activeNode ? (
                reorder.activeNode.mimeType === MIME_FOLDER ? (
                  <TreeNode node={reorder.activeNode} parentFolderId={undefined} depth={0} />
                ) : (
                  <FileRow
                    node={reorder.activeNode}
                    driveId={drive.id}
                    parentFolderId={undefined}
                    activeDriveId={activeDriveId}
                    iconOverride={docIconOverrides.get(reorder.activeNode.id)}
                    depth={0}
                  />
                )
              ) : null}
            </DragOverlay>
          </DndContext>
        )}
      </SidebarGroup>
    </DriveSectionProvider>
  );
}
