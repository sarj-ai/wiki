"use client";

import {
  closestCorners,
  type CollisionDetection,
  DndContext,
  DragOverlay,
  pointerWithin,
  rectIntersection,
} from "@dnd-kit/core";
import { SortableContext, verticalListSortingStrategy } from "@dnd-kit/sortable";
import { ChevronRight, HardDrive, Loader2, RefreshCw } from "lucide-react";
import { toast } from "sonner";

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
import type { DriveNode, DriveSummary, FolderIcon, SortOrder } from "@/types/wiki";
import { MIME_DOC, MIME_FOLDER, MIME_HTML } from "@/types/wiki";

import { CreateFileMenu } from "./create-file-menu";
import { DriveSectionProvider, type DriveSectionValue } from "./drive-context";
import { EmptyDropZone } from "./empty-droppable";
import { FileRow } from "./file-row";
import { SortableItem } from "./sortable-item";
import { TreeNode } from "./tree-node";
import { useIsCrossContainerDropTarget } from "./use-drag-over-store";
import { useDriveTreeIndex } from "./use-drive-tree-index";
import { useWikiReorder } from "./use-wiki-reorder";

interface DriveSectionProps {
  activeDriveId: null | string;
  drive: DriveSummary;
  folderIcons: FolderIcon[];
  showNonDocFiles: boolean;
  sortOrders: SortOrder[];
}

interface BuildRootItemsArgs {
  childrenByParent: Map<string, DriveNode[]>;
  rootFiles: DriveNode[];
  showNonDocFiles: boolean;
  sortOrderMap: Map<string, number>;
}

const cursorFirstCollision: CollisionDetection = (args) => {
  const pointer = pointerWithin(args);
  if (pointer.length > 0) return pointer;
  const intersect = rectIntersection(args);
  if (intersect.length > 0) return intersect;
  return closestCorners(args);
};

export function DriveSection({
  activeDriveId,
  drive,
  folderIcons,
  showNonDocFiles,
  sortOrders,
}: DriveSectionProps) {
  const [expanded, setExpanded] = useExpandedDrive(drive.id);
  const {
    childrenByParent,
    docIconOverrides,
    filesByParent,
    filesComplete,
    iconOverrides,
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
    showNonDocFiles,
    sortOrderMap,
  });
  const optimisticOrder = reorder.optimisticByContainer.get(rootContainerId) ?? null;
  const displayItems = applyOptimisticOrder(rootItems, optimisticOrder);
  const displayItemIds = displayItems.map((item) => item.node.id);

  const contextValue: DriveSectionValue = {
    activeDriveId,
    childrenByParent,
    docIconOverrides,
    driveId: drive.id,
    filesByParent,
    iconOverrides,
    optimisticByContainer: reorder.optimisticByContainer,
    showNonDocFiles,
    sortOrderMap,
    treeFilesComplete: filesComplete,
  };

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
              aria-expanded={expanded}
              className="flex w-full items-center gap-1 pr-7"
              onClick={() => {
                setExpanded(!expanded);
              }}
              type="button"
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
              aria-label="Refresh drive"
              className="size-6 shrink-0"
              disabled={refreshDrive.isPending}
              onClick={handleRefresh}
              size="icon-xs"
              title="Refresh drive — pulls the latest from Drive instead of waiting for the cache to expire"
              type="button"
              variant="ghost"
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
        {expanded ? (
          <DndContext
            collisionDetection={cursorFirstCollision}
            onDragCancel={reorder.onDragCancel}
            onDragEnd={reorder.onDragEnd}
            onDragOver={reorder.onDragOver}
            onDragStart={reorder.onDragStart}
            sensors={reorder.sensors}
          >
            <SidebarGroupContent>
              <SidebarMenu>
                {treeQuery.isPending || (!treeQuery.data && treeQuery.isFetching) ? (
                  <DriveTreeSkeleton />
                ) : null}
                {treeQuery.isError && !treeQuery.isFetching && !treeQuery.data ? (
                  <SidebarMenuItem>
                    <span className="text-destructive px-2 py-1 text-xs">Failed to load drive</span>
                  </SidebarMenuItem>
                ) : null}
                {!treeQuery.isLoading && (
                  <SortableContext
                    id={rootContainerId}
                    items={displayItemIds}
                    strategy={verticalListSortingStrategy}
                  >
                    {displayItems.map(({ node, type }) => (
                      <SortableItem
                        data={{ isFolder: type === "folder" }}
                        id={node.id}
                        key={node.id}
                        variant="item"
                      >
                        {type === "folder" ? (
                          <TreeNode depth={0} node={node} parentFolderId={undefined} />
                        ) : (
                          <FileRow
                            activeDriveId={activeDriveId}
                            depth={0}
                            driveId={drive.id}
                            iconOverride={docIconOverrides.get(node.id)}
                            node={node}
                            parentFolderId={undefined}
                          />
                        )}
                      </SortableItem>
                    ))}
                  </SortableContext>
                )}
                {!treeQuery.isLoading && rootItems.length === 0 && (
                  <EmptyDropZone label="Empty drive" parentId={drive.id} variant="item" />
                )}
              </SidebarMenu>
            </SidebarGroupContent>

            <DragOverlay dropAnimation={null}>
              {reorder.activeNode ? (
                reorder.activeNode.mimeType === MIME_FOLDER ? (
                  <TreeNode depth={0} node={reorder.activeNode} parentFolderId={undefined} />
                ) : (
                  <FileRow
                    activeDriveId={activeDriveId}
                    depth={0}
                    driveId={drive.id}
                    iconOverride={docIconOverrides.get(reorder.activeNode.id)}
                    node={reorder.activeNode}
                    parentFolderId={undefined}
                  />
                )
              ) : null}
            </DragOverlay>
          </DndContext>
        ) : null}
      </SidebarGroup>
    </DriveSectionProvider>
  );
}

function buildRootItems({
  childrenByParent,
  rootFiles,
  showNonDocFiles,
  sortOrderMap,
}: BuildRootItemsArgs): KindedNode[] {
  const folders: KindedNode[] = (childrenByParent.get("__root__") ?? []).map((node) => ({
    node,
    type: "folder",
  }));

  const rawFiles = rootFiles.filter((n) => !isFolderLikeNode(n));
  const visibleFiles = showNonDocFiles
    ? rawFiles
    : rawFiles.filter((n) => n.mimeType === MIME_DOC || n.mimeType === MIME_HTML);
  const files: KindedNode[] = visibleFiles.map((node) => ({ node, type: "file" }));
  return [...folders, ...files].sort((a, b) => compareNodeOrder(a, b, sortOrderMap));
}

function DriveTreeSkeleton() {
  return (
    <SidebarMenuItem className="flex flex-col gap-1.5 px-2 py-1">
      <div className="flex items-center gap-2 pl-1">
        <Skeleton className="size-3.5 rounded-sm" />
        <Skeleton className="h-3.5 w-32 rounded-sm" />
      </div>
      <div className="flex items-center gap-2 pl-5">
        <Skeleton className="size-3.5 rounded-sm" />
        <Skeleton className="h-3.5 w-40 rounded-sm" />
      </div>
      <div className="flex items-center gap-2 pl-5">
        <Skeleton className="size-3.5 rounded-sm" />
        <Skeleton className="h-3.5 w-24 rounded-sm" />
      </div>
      <div className="flex items-center gap-2 pl-1">
        <Skeleton className="size-3.5 rounded-sm" />
        <Skeleton className="h-3.5 w-36 rounded-sm" />
      </div>
    </SidebarMenuItem>
  );
}
