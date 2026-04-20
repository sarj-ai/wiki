"use client";

import { useState, useMemo } from "react";
import { ChevronRight, HardDrive } from "lucide-react";
import {
  DndContext,
  DragOverlay,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
  type DragStartEvent,
} from "@dnd-kit/core";
import { SortableContext, arrayMove, verticalListSortingStrategy } from "@dnd-kit/sortable";
import { useQueryClient } from "@tanstack/react-query";
import { navigatorKeys } from "@/hooks/use-navigator-tree";
import { toast } from "sonner";
import {
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarMenu,
  SidebarMenuItem,
} from "@/components/ui/sidebar";
import { cn } from "@/lib/utils";
import { useDocIcons, useDriveTree, useFolderFiles } from "@/hooks/use-navigator-tree";
import { useExpandedDrive } from "@/hooks/use-expanded-folders";
import { useBulkSetSortOrders } from "@/hooks/use-navigator-mutations";
import type { DriveNode, DriveSummary, FolderIcon, SortOrder } from "@/types/navigator";
import { MIME_DOC, MIME_FOLDER } from "@/types/navigator";
import { TreeNode } from "./tree-node";
import { FileRow } from "./file-row";
import { CreateFileMenu } from "./create-file-menu";
import { SortableItem } from "./sortable-item";

interface DriveSectionProps {
  drive: DriveSummary;
  activeDriveId: string | null;
  showNonDocFiles: boolean;
  folderIcons: FolderIcon[];
  sortOrders: SortOrder[];
}

function buildChildrenIndex(folders: DriveNode[]): Map<string, DriveNode[]> {
  const map = new Map<string, DriveNode[]>();
  for (const folder of folders) {
    const key = folder.parentId ?? "__root__";
    const bucket = map.get(key);
    if (bucket) bucket.push(folder);
    else map.set(key, [folder]);
  }
  return map;
}

type RootItem = { type: "folder"; node: DriveNode } | { type: "file"; node: DriveNode };

// Single sort pass across folders AND files together. Notion-style: a
// user drop that lands a file above a folder must survive the
// round-trip; if we sorted each category separately the displayed
// order would revert from "interleaved" back to "folders first, files
// after" the moment the optimistic state clears, and dnd-kit's FLIP
// animation would bounce items to their new rows.
function sortRootItems(items: RootItem[], sortOrderMap: Map<string, number>): RootItem[] {
  return [...items].sort((a, b) => {
    const ao = sortOrderMap.get(a.node.id);
    const bo = sortOrderMap.get(b.node.id);
    if (ao !== undefined && bo !== undefined) return ao - bo;
    if (ao !== undefined) return -1;
    if (bo !== undefined) return 1;
    // No sort order set on either side: folders before files, then alpha.
    if (a.type !== b.type) return a.type === "folder" ? -1 : 1;
    return a.node.name.localeCompare(b.node.name);
  });
}

export function DriveSection({
  drive,
  activeDriveId,
  showNonDocFiles,
  folderIcons,
  sortOrders,
}: DriveSectionProps) {
  const [expanded, setExpanded] = useExpandedDrive(drive.id);
  const treeQuery = useDriveTree(drive.id);
  const rootFilesQuery = useFolderFiles(drive.id, expanded);

  const folderIconMap = useMemo(() => {
    const map = new Map<string, string | null>();
    for (const f of folderIcons) map.set(f.folderDriveId, f.icon);
    return map;
  }, [folderIcons]);

  const docIconsQuery = useDocIcons();
  const docIconOverrides = useMemo(() => {
    const map = new Map<string, string | null>();
    for (const d of docIconsQuery.data ?? []) map.set(d.driveId, d.icon);
    return map;
  }, [docIconsQuery.data]);

  const sortOrderMap = useMemo(() => {
    const map = new Map<string, number>();
    for (const s of sortOrders) map.set(s.driveId, s.sortOrder);
    return map;
  }, [sortOrders]);

  const childrenByParent = useMemo(
    () => buildChildrenIndex(treeQuery.data?.folders ?? []),
    [treeQuery.data],
  );

  const rootItems = useMemo((): RootItem[] => {
    const folders: RootItem[] = (childrenByParent.get("__root__") ?? []).map((node) => ({
      type: "folder",
      node,
    }));
    const rawFiles = (rootFilesQuery.data ?? []).filter((n) => n.mimeType !== MIME_FOLDER);
    const visibleFiles = showNonDocFiles
      ? rawFiles
      : rawFiles.filter((n) => n.mimeType === MIME_DOC);
    const files: RootItem[] = visibleFiles.map((node) => ({ type: "file", node }));
    return sortRootItems([...folders, ...files], sortOrderMap);
  }, [childrenByParent, rootFilesQuery.data, showNonDocFiles, sortOrderMap]);

  const rootItemIds = useMemo(() => rootItems.map((i) => i.node.id), [rootItems]);

  // Optimistic order per SortableContext container, keyed by the
  // container id (``parent:<drive-or-folder-id>``). Set synchronously
  // inside handleDragEnd so the reorder paints in the same commit as
  // dnd-kit's DragEnd dispatch, and — critically for subfolder drags
  // — bypasses the 4-render prop-drilling lag between React Query's
  // sortOrders cache update and TreeNode's re-render with the new
  // sortOrderMap. Without this, TreeNode kept rendering the old order
  // for several frames after drop, reading as a title "bounce".
  const [optimisticByContainer, setOptimisticByContainer] = useState<Map<string, string[]>>(
    () => new Map(),
  );
  const rootContainerId = `parent:${drive.id}`;
  const optimisticItemIds = optimisticByContainer.get(rootContainerId) ?? null;

  const displayItemIds = optimisticItemIds ?? rootItemIds;

  const displayItems = useMemo((): RootItem[] => {
    if (!optimisticItemIds) return rootItems;
    const byId = new Map<string, RootItem>(rootItems.map((item) => [item.node.id, item]));
    return optimisticItemIds
      .map((id) => byId.get(id))
      .filter((item): item is RootItem => item !== undefined);
  }, [rootItems, optimisticItemIds]);

  const bulkSetSortOrders = useBulkSetSortOrders();
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 5 } }));

  // Drag overlay: render the dragged row in a portal following the
  // cursor, while the source row hides (stays in flow, invisible).
  // The source never gets a drag transform applied, so the whole
  // class of "transform clears then children re-layout" animations on
  // the source are avoided. The overlay node is looked up from the
  // React Query cache so we can show any row type (drive-root or
  // subfolder, folder or file) without threading data through props.
  const qc = useQueryClient();
  const [activeNode, setActiveNode] = useState<DriveNode | null>(null);
  const findNodeById = (id: string): DriveNode | null => {
    const tree = qc.getQueryData<{ folders: DriveNode[] }>(navigatorKeys.driveTree(drive.id));
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
  const handleDragStart = (event: DragStartEvent) => {
    setActiveNode(findNodeById(String(event.active.id)));
  };

  const handleDragEnd = async (event: DragEndEvent) => {
    setActiveNode(null);
    const { active, over } = event;
    if (!over || active.id === over.id) return;

    const activeContainerId = active.data.current?.sortable?.containerId as string | undefined;
    const overContainerId = over.data.current?.sortable?.containerId as string | undefined;
    if (!activeContainerId || !overContainerId || activeContainerId !== overContainerId) return;

    const items = (active.data.current?.sortable?.items ?? []) as string[];
    const oldIndex = items.indexOf(String(active.id));
    const newIndex = items.indexOf(String(over.id));
    if (oldIndex < 0 || newIndex < 0) return;

    const reordered = arrayMove(items, oldIndex, newIndex);

    // Optimistic per-container reorder. Works for BOTH drive root and
    // every subfolder — the display layer reads this map before falling
    // back to sortOrderMap-based sorting, so rows land in the new order
    // on the same frame the drop fires.
    setOptimisticByContainer((prev) => {
      const next = new Map(prev);
      next.set(activeContainerId, reordered);
      return next;
    });

    const updates = reordered.map((id, i) => ({ driveId: id, sortOrder: i + 1 }));
    try {
      await bulkSetSortOrders.mutateAsync(updates);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Reorder failed";
      toast.error(message);
    } finally {
      setOptimisticByContainer((prev) => {
        if (!prev.has(activeContainerId)) return prev;
        const next = new Map(prev);
        next.delete(activeContainerId);
        return next;
      });
    }
  };

  return (
    <SidebarGroup>
      <div className="group/driveheader relative">
        <SidebarGroupLabel asChild>
          <button
            type="button"
            onClick={() => setExpanded(!expanded)}
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
          sensors={sensors}
          onDragStart={handleDragStart}
          onDragEnd={handleDragEnd}
          onDragCancel={() => setActiveNode(null)}
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
                  id={`parent:${drive.id}`}
                  items={displayItemIds}
                  strategy={verticalListSortingStrategy}
                >
                  {displayItems.map(({ type, node }) =>
                    type === "folder" ? (
                      <SortableItem key={node.id} id={node.id} variant="item">
                        <TreeNode
                          node={node}
                          driveId={drive.id}
                          parentFolderId={null}
                          childrenByParent={childrenByParent}
                          sortOrderMap={sortOrderMap}
                          optimisticByContainer={optimisticByContainer}
                          activeDriveId={activeDriveId}
                          showNonDocFiles={showNonDocFiles}
                          folderIconOverride={folderIconMap.get(node.id) ?? null}
                          iconOverrides={folderIconMap}
                          docIconOverrides={docIconOverrides}
                          depth={0}
                        />
                      </SortableItem>
                    ) : (
                      <SortableItem key={node.id} id={node.id} variant="item">
                        <FileRow
                          node={node}
                          driveId={drive.id}
                          parentFolderId={null}
                          activeDriveId={activeDriveId}
                          iconOverride={docIconOverrides.get(node.id) ?? null}
                          depth={0}
                        />
                      </SortableItem>
                    ),
                  )}
                </SortableContext>
              )}
              {rootFilesQuery.isLoading && (
                <SidebarMenuItem>
                  <span className="text-muted-foreground px-2 py-1 text-xs">Loading files…</span>
                </SidebarMenuItem>
              )}
              {!treeQuery.isLoading &&
                rootItems.length === 0 &&
                !rootFilesQuery.isLoading && (
                  <SidebarMenuItem>
                    <span className="text-muted-foreground px-2 py-1 text-xs">Empty drive</span>
                  </SidebarMenuItem>
                )}
            </SidebarMenu>
          </SidebarGroupContent>
          {/*
            Portal-rendered preview of the row following the cursor.
            ``dropAnimation={null}`` disables dnd-kit's "overlay flies
            back to source slot on drop" animation — that tween was
            ~200ms and read to users as a bounce when the source had
            already rearranged to its new position. With null, the
            overlay unmounts instantly; React's DOM reorder handles
            the final position.
          */}
          <DragOverlay dropAnimation={null}>
            {activeNode ? (
              activeNode.mimeType === MIME_FOLDER ? (
                <TreeNode
                  node={activeNode}
                  driveId={drive.id}
                  parentFolderId={null}
                  childrenByParent={childrenByParent}
                  sortOrderMap={sortOrderMap}
                  optimisticByContainer={optimisticByContainer}
                  activeDriveId={activeDriveId}
                  showNonDocFiles={showNonDocFiles}
                  folderIconOverride={folderIconMap.get(activeNode.id) ?? null}
                  iconOverrides={folderIconMap}
                  docIconOverrides={docIconOverrides}
                  depth={0}
                />
              ) : (
                <FileRow
                  node={activeNode}
                  driveId={drive.id}
                  parentFolderId={null}
                  activeDriveId={activeDriveId}
                  iconOverride={docIconOverrides.get(activeNode.id) ?? null}
                  depth={0}
                />
              )
            ) : null}
          </DragOverlay>
        </DndContext>
      )}
    </SidebarGroup>
  );
}
