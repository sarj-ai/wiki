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
import { useWikiReorder } from "./use-wiki-reorder";

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
  const reorder = useWikiReorder(drive.id);
  const refreshDrive = useRefreshDrive();

  const handleRefresh = (e: React.MouseEvent) => {
    // The header is itself a button that toggles expand on click. Stop
    // propagation so the user's refresh click doesn't accidentally
    // collapse the drive.
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
            // Cursor-following collision: prefer whatever droppable
            // the cursor is *inside*; fall back to closestCorners
            // when the cursor is in empty space (e.g. above the
            // first row or below the last row in an otherwise
            // sparse drive). closestCenter alone doesn't follow the
            // cursor — it ranks by the dragged element's CENTER, so
            // dragging an overlay far up while the cursor still
            // hovers a sibling kept reporting the sibling as `over`
            // and drops collapsed to same-container reorders.
            collisionDetection={cursorFirstCollision}
            onDragStart={reorder.onDragStart}
            onDragOver={reorder.onDragOver}
            onDragEnd={reorder.onDragEnd}
            onDragCancel={reorder.onDragCancel}
          >
            <SidebarGroupContent>
              <SidebarMenu>
                {/*
                  Same gating as the file detail page: treat "errored
                  but still trying" as loading. Without this, hard
                  refresh during the access-token-expired window flashes
                  "Failed to load drive" before ky's 401 → refresh →
                  replay (and React Query's retry) lands the data.
                */}
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
                  <EmptyDropZone parentId={drive.id} variant="item" label="Empty drive" />
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

/**
 * Collision detection that prefers droppables under the user's
 * cursor over droppables closest to the dragged element's center.
 *
 * The default sortable strategy ranks by ``active.rect``'s center vs.
 * each droppable's center — fine when the cursor is roughly aligned
 * with the dragged element, but with grab-at-edge or any non-center
 * grab the dragged overlay can sit far from the cursor, and the
 * "over" stays glued to the row visually under the OVERLAY rather
 * than the row visually under the CURSOR. Users feel that as
 * "drops keep going to the wrong place".
 *
 * Layered:
 *   1. ``pointerWithin`` — exact "cursor is inside this rect"
 *      collisions. Highest fidelity when present.
 *   2. ``rectIntersection`` — second pass that catches drops where
 *      the cursor is at a row's edge but the rect math misses it.
 *   3. ``closestCorners`` — fallback for cursors in empty space (the
 *      first/last row edges, or an empty SortableContext). Picks
 *      the droppable whose corners are closest to the active rect.
 *      Better than closestCenter for stacked rows of varying height.
 */
const cursorFirstCollision: CollisionDetection = (args) => {
  const pointer = pointerWithin(args);
  if (pointer.length > 0) return pointer;
  const intersect = rectIntersection(args);
  if (intersect.length > 0) return intersect;
  return closestCorners(args);
};

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
  // Strip folder-likes (native folders + folder-target shortcuts)
  // from the lazy file list — the tree walk already surfaces them in
  // ``childrenByParent``. Without this filter the same shortcut id
  // appears twice and React renders the second copy as a FileRow,
  // routing folder-shortcut clicks to ``/d/<targetFolderId>`` (which
  // Google has no preview for).
  const rawFiles = rootFiles.filter((n) => !isFolderLikeNode(n));
  const visibleFiles = showNonDocFiles ? rawFiles : rawFiles.filter((n) => n.mimeType === MIME_DOC);
  const files: KindedNode[] = visibleFiles.map((node) => ({ type: "file", node }));
  return [...folders, ...files].sort((a, b) => compareNodeOrder(a, b, sortOrderMap));
}

// A handful of skeleton rows shaped like folder/file entries so the
// loading state matches the rhythm of real content. Varied widths and
// indentation keep the pulse from looking like a single bar.
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
