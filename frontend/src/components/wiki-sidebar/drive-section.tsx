"use client";

import { useDroppable } from "@dnd-kit/core";
import { SortableContext, verticalListSortingStrategy } from "@dnd-kit/sortable";
import {
  ChevronRight,
  ExternalLink,
  HardDrive,
  Home,
  Loader2,
  MoreHorizontal,
  RefreshCw,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
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
import { folderUrlFor } from "@/lib/file-urls";
import { cn } from "@/lib/utils";
import { EMPTY_NODES, indexSidebarNodes, indexVisibleDocuments } from "@/lib/wiki-indexes";
import { buildSiblingItems, type KindedNode, orderSiblingItems } from "@/lib/wiki-tree";
import type { DocIcon, DriveNode, DriveSummary, FolderIcon, SortOrder } from "@/types/wiki";

import { CreateFileMenu } from "./create-file-menu";
import { DriveAccessSheet } from "./drive-access-sheet";
import { DriveSectionProvider, type DriveSectionValue } from "./drive-context";
import { EmptyDropZone } from "./empty-drop-zone";
import { FileRow } from "./file-row";
import { SortableItem } from "./sortable-item";
import { TreeNode } from "./tree-node";
import { useIsCrossContainerDropTarget } from "./use-drag-over-store";
import { useDrivePredictiveLoading } from "./use-drive-predictive-loading";
import { useDriveTreeIndex } from "./use-drive-tree-index";

const EMPTY_ROOT_ITEMS: readonly KindedNode[] = [];

export function DriveSection({
  activeDriveId,
  defaultExpanded,
  docIcons,
  drive,
  folderIcons,
  optimisticByContainer,
  pathname,
  showNonDocFiles,
  sortOrders,
}: DriveSectionProps) {
  const [expanded, setExpanded] = useExpandedDrive(drive.id, defaultExpanded);
  const predictiveLoading = useDrivePredictiveLoading(drive.id, expanded);
  const {
    childrenByParent,
    docIconOverrides,
    filesByParent,
    filesComplete,
    iconOverrides,
    sortOrderMap,
    treeQuery,
  } = useDriveTreeIndex(drive.id, docIcons, folderIcons, sortOrders, expanded);
  const lazyRootFiles = useFolderFiles(drive.id, expanded && !filesComplete);
  const refreshDrive = useRefreshDrive();

  const handleRefresh = () => {
    refreshDrive.mutate(drive.id, {
      onError: (err) => {
        const message = err instanceof Error ? err.message : "Refresh failed";
        toast.error(message);
      },
    });
  };

  const rootContainerId = `parent:${drive.id}`;
  const { setNodeRef: setRootDropRef } = useDroppable({
    data: { driveId: drive.id, parentFolderId: undefined },
    disabled: expanded,
    id: rootContainerId,
  });
  const isDropTarget = useIsCrossContainerDropTarget(rootContainerId);
  const rootFiles = filesComplete
    ? (filesByParent.get("__root__") ?? EMPTY_NODES)
    : (lazyRootFiles.data ?? EMPTY_NODES);
  const optimisticOrder = optimisticByContainer.get(rootContainerId) ?? null;
  const rootItems = expanded
    ? buildRootItems({
        childrenByParent,
        optimisticOrder,
        rootFiles,
        showNonDocFiles,
        sortOrderMap,
      })
    : EMPTY_ROOT_ITEMS;
  const displayItems = rootItems;
  const displayItemIds = displayItems.map((item) => item.node.id);

  const contextValue: DriveSectionValue = {
    activeDriveId,
    childrenByParent,
    docIconOverrides,
    driveId: drive.id,
    filesByParent,
    iconOverrides,
    optimisticByContainer,
    pathname,
    showNonDocFiles,
    sortOrderMap,
    treeFilesComplete: filesComplete,
  };

  return (
    <DriveSectionProvider value={contextValue}>
      <SidebarGroup>
        <div className="group/driveheader relative" ref={setRootDropRef}>
          <SidebarGroupLabel
            asChild
            className={cn(
              "rounded-md transition-[background-color,box-shadow] duration-150",

              isDropTarget && "ring-primary/60 bg-primary/10 ring-1",
            )}
          >
            <Button
              aria-expanded={expanded}
              className="flex h-8 w-full items-center justify-start gap-1 px-2 py-0 text-xs"
              data-sidebar-row="drive"
              onBlur={predictiveLoading.onBlur}
              onClick={() => {
                setExpanded(!expanded);
              }}
              onFocus={predictiveLoading.onFocus}
              onPointerCancel={predictiveLoading.onPointerLeave}
              onPointerDown={predictiveLoading.onPointerDown}
              onPointerEnter={predictiveLoading.onPointerEnter}
              onPointerLeave={predictiveLoading.onPointerLeave}
              type="button"
              variant="ghost"
            >
              <ChevronRight
                className={cn("size-3 transition-transform", expanded && "rotate-90")}
              />
              {drive.isPersonal ? (
                <Home className="size-3.5" />
              ) : (
                <HardDrive className="size-3.5" />
              )}
              <span className="truncate">{drive.name}</span>
            </Button>
          </SidebarGroupLabel>
          <div
            className="absolute top-1 right-1 flex items-center gap-0.5 opacity-0 group-hover/driveheader:opacity-100 focus-within:opacity-100"
            data-sidebar-row-actions="drive"
          >
            <DriveAccessSheet drive={drive} />
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button
                  aria-label={`More actions for ${drive.name}`}
                  className="size-5 shrink-0"
                  onClick={(event) => {
                    event.stopPropagation();
                  }}
                  size="icon-xs"
                  type="button"
                  variant="ghost"
                >
                  <MoreHorizontal className="size-3.5" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-48">
                <DropdownMenuItem disabled={refreshDrive.isPending} onClick={handleRefresh}>
                  {refreshDrive.isPending ? (
                    <Loader2 className="size-4 animate-spin" />
                  ) : (
                    <RefreshCw className="size-4" />
                  )}
                  Refresh from Drive
                </DropdownMenuItem>
                <DropdownMenuItem asChild>
                  <a href={folderUrlFor(drive.id)} rel="noopener noreferrer" target="_blank">
                    <ExternalLink className="size-4" />
                    Open in Google Drive
                  </a>
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
            <CreateFileMenu driveId={drive.id} parentFolderId={drive.id} parentName={drive.name} />
          </div>
        </div>
        {expanded ? (
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
                      data={{
                        driveId: drive.id,
                        isFolder: type === "folder",
                        parentFolderId: undefined,
                      }}
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
                          pathname={pathname}
                        />
                      )}
                    </SortableItem>
                  ))}
                </SortableContext>
              )}
              {!treeQuery.isLoading && rootItems.length === 0 && (
                <EmptyDropZone
                  driveId={drive.id}
                  label="Empty drive"
                  parentId={drive.id}
                  variant="item"
                />
              )}
            </SidebarMenu>
          </SidebarGroupContent>
        ) : null}
      </SidebarGroup>
    </DriveSectionProvider>
  );
}

interface DriveSectionProps {
  activeDriveId: null | string;
  defaultExpanded: boolean;
  docIcons: readonly DocIcon[];
  drive: DriveSummary;
  folderIcons: readonly FolderIcon[];
  optimisticByContainer: Map<string, string[]>;
  pathname: string;
  showNonDocFiles: boolean;
  sortOrders: readonly SortOrder[];
}

interface BuildRootItemsArgs {
  childrenByParent: Map<string, DriveNode[]>;
  optimisticOrder: null | readonly string[];
  rootFiles: readonly DriveNode[];
  showNonDocFiles: boolean;
  sortOrderMap: Map<string, number>;
}

function buildRootItems({
  childrenByParent,
  optimisticOrder,
  rootFiles,
  showNonDocFiles,
  sortOrderMap,
}: BuildRootItemsArgs): readonly KindedNode[] {
  const folders = childrenByParent.get("__root__") ?? EMPTY_NODES;
  const rawFiles = indexSidebarNodes(rootFiles).files;
  const visibleFiles = showNonDocFiles ? rawFiles : indexVisibleDocuments(rawFiles);
  return orderSiblingItems(buildSiblingItems(folders, visibleFiles), sortOrderMap, optimisticOrder);
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
