"use client";

import { DndContext, DragOverlay } from "@dnd-kit/core";
import { SortableContext, verticalListSortingStrategy } from "@dnd-kit/sortable";
import { ChevronRight, HardDrive } from "lucide-react";

import type { DriveNode, DriveSummary, FolderIcon, SortOrder } from "@/types/navigator";

import {
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarMenu,
  SidebarMenuItem,
} from "@/components/ui/sidebar";
import { useExpandedDrive } from "@/hooks/use-expanded-folders";
import { useFolderFiles } from "@/hooks/use-navigator-tree";
import {
  applyOptimisticOrder,
  compareNodeOrder,
  type KindedNode,
} from "@/lib/navigator-tree";
import { cn } from "@/lib/utils";
import { MIME_DOC, MIME_FOLDER } from "@/types/navigator";

import { CreateFileMenu } from "./create-file-menu";
import { DriveSectionProvider, type DriveSectionValue } from "./drive-context";
import { FileRow } from "./file-row";
import { SortableItem } from "./sortable-item";
import { TreeNode } from "./tree-node";
import { useIsCrossContainerDropTarget } from "./use-drag-over-store";
import { useDriveTreeIndex } from "./use-drive-tree-index";
import { useNavigatorReorder } from "./use-navigator-reorder";

interface DriveSectionProps {
  drive: DriveSummary;
  activeDriveId: string | null;
  showNonDocFiles: boolean;
  folderIcons: FolderIcon[];
  sortOrders: SortOrder[];
}

export function DriveSection({
  drive,
  activeDriveId,
  showNonDocFiles,
  folderIcons,
  sortOrders,
}: DriveSectionProps) {
  const [expanded, setExpanded] = useExpandedDrive(drive.id);
  const { childrenByParent, iconOverrides, docIconOverrides, sortOrderMap, treeQuery } =
    useDriveTreeIndex(drive.id, folderIcons, sortOrders);
  const rootFilesQuery = useFolderFiles(drive.id, expanded);
  const reorder = useNavigatorReorder(drive.id);

  const rootContainerId = `parent:${drive.id}`;
  const isDropTarget = useIsCrossContainerDropTarget(rootContainerId);
  const rootItems = buildRootItems({
    childrenByParent,
    rootFiles: rootFilesQuery.data ?? [],
    sortOrderMap,
    showNonDocFiles,
  });
  const optimisticOrder = reorder.optimisticByContainer.get(rootContainerId) ?? null;
  const displayItems = applyOptimisticOrder(rootItems, optimisticOrder);
  const displayItemIds = displayItems.map((item) => item.node.id);

  const contextValue: DriveSectionValue = {
    driveId: drive.id,
    activeDriveId,
    showNonDocFiles,
    childrenByParent,
    iconOverrides,
    docIconOverrides,
    sortOrderMap,
    optimisticByContainer: reorder.optimisticByContainer,
  };

  return (
    <DriveSectionProvider value={contextValue}>
      <SidebarGroup>
        <div className="group/driveheader relative">
          <SidebarGroupLabel
            asChild
            className={cn(
              "rounded-md transition-[background-color,box-shadow] duration-150",
              // Highlight the drive header when it's the active
              // cross-container drop target — gives the user a clear
              // signal that releasing here will move the dragged row
              // into this drive's root.
              isDropTarget && "ring-primary/60 bg-primary/10 ring-1",
            )}
          >
            <button
              type="button"
              onClick={() => { setExpanded(!expanded); }}
              className="flex w-full items-center gap-1 pr-7"
              aria-expanded={expanded}
            >
              <ChevronRight className={cn("size-3 transition-transform", expanded && "rotate-90")} />
              <HardDrive className="size-3.5" />
              <span className="truncate">{drive.name}</span>
            </button>
          </SidebarGroupLabel>
          <div className="absolute top-1 right-1 opacity-0 group-hover/driveheader:opacity-100 focus-within:opacity-100">
            <CreateFileMenu parentFolderId={drive.id} />
          </div>
        </div>
        {expanded && (
          <DndContext
            sensors={reorder.sensors}
            onDragStart={reorder.onDragStart}
            onDragOver={reorder.onDragOver}
            onDragEnd={reorder.onDragEnd}
            onDragCancel={reorder.onDragCancel}
          >
            <SidebarGroupContent>
              <SidebarMenu>
                {treeQuery.isLoading && (
                  <SidebarMenuItem>
                    <span className="text-muted-foreground px-2 py-1 text-xs">Loading folders…</span>
                  </SidebarMenuItem>
                )}
                {treeQuery.isError && (
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
                          <TreeNode node={node} parentFolderId={null} depth={0} />
                        ) : (
                          <FileRow
                            node={node}
                            driveId={drive.id}
                            parentFolderId={null}
                            activeDriveId={activeDriveId}
                            iconOverride={docIconOverrides.get(node.id) ?? null}
                            depth={0}
                          />
                        )}
                      </SortableItem>
                    ))}
                  </SortableContext>
                )}
                {rootFilesQuery.isLoading && (
                  <SidebarMenuItem>
                    <span className="text-muted-foreground px-2 py-1 text-xs">Loading files…</span>
                  </SidebarMenuItem>
                )}
                {!treeQuery.isLoading && rootItems.length === 0 && !rootFilesQuery.isLoading && (
                  <SidebarMenuItem>
                    <span className="text-muted-foreground px-2 py-1 text-xs">Empty drive</span>
                  </SidebarMenuItem>
                )}
              </SidebarMenu>
            </SidebarGroupContent>
            {/*
              Portal-rendered preview of the row following the cursor.
              ``dropAnimation={null}`` disables dnd-kit's "overlay flies
              back to source slot on drop" tween — that ~200ms animation
              read to users as a bounce when the source had already
              rearranged to its new position. With null, the overlay
              unmounts instantly; React's DOM reorder handles the final
              position.
            */}
            <DragOverlay dropAnimation={null}>
              {reorder.activeNode ? (
                reorder.activeNode.mimeType === MIME_FOLDER ? (
                  <TreeNode node={reorder.activeNode} parentFolderId={null} depth={0} />
                ) : (
                  <FileRow
                    node={reorder.activeNode}
                    driveId={drive.id}
                    parentFolderId={null}
                    activeDriveId={activeDriveId}
                    iconOverride={docIconOverrides.get(reorder.activeNode.id) ?? null}
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

interface BuildRootItemsArgs {
  childrenByParent: Map<string, DriveNode[]>;
  rootFiles: DriveNode[];
  sortOrderMap: Map<string, number>;
  showNonDocFiles: boolean;
}

// Combine root-level folders (from the cached drive tree) and root-
// level files (from a lazy folder-files call keyed by drive id) into
// a single sorted list. Single sort pass interleaves the two so a
// drop that lands a file above a folder survives the round-trip.
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
  const rawFiles = rootFiles.filter((n) => n.mimeType !== MIME_FOLDER);
  const visibleFiles = showNonDocFiles
    ? rawFiles
    : rawFiles.filter((n) => n.mimeType === MIME_DOC);
  const files: KindedNode[] = visibleFiles.map((node) => ({ type: "file", node }));
  return [...folders, ...files].sort((a, b) => compareNodeOrder(a, b, sortOrderMap));
}
