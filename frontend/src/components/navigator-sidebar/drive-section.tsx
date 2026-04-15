"use client";

import { useMemo } from "react";
import { ChevronRight, HardDrive } from "lucide-react";
import { SortableContext, verticalListSortingStrategy } from "@dnd-kit/sortable";
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
  // Group by parent_id. A null parent_id means the folder sits directly
  // under the drive root — we key those under the literal "__root__".
  const map = new Map<string, DriveNode[]>();
  for (const folder of folders) {
    const key = folder.parentId ?? "__root__";
    const bucket = map.get(key);
    if (bucket) bucket.push(folder);
    else map.set(key, [folder]);
  }
  return map;
}

function sortNodes(nodes: DriveNode[], sortOrderByDriveId: Map<string, number>): DriveNode[] {
  return [...nodes].sort((a, b) => {
    const aOverride = sortOrderByDriveId.get(a.id);
    const bOverride = sortOrderByDriveId.get(b.id);
    if (aOverride !== undefined && bOverride !== undefined) return aOverride - bOverride;
    if (aOverride !== undefined) return -1;
    if (bOverride !== undefined) return 1;
    return a.name.localeCompare(b.name);
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
  // Drive-root files: treat the drive id as a folder id and lazy-load on
  // expand. Backend maps directly to `'{drive_id}' in parents` which Drive
  // accepts for drive-root children.
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

  const rootFolders = useMemo(
    () => sortNodes(childrenByParent.get("__root__") ?? [], sortOrderMap),
    [childrenByParent, sortOrderMap],
  );

  const rootFiles = useMemo(() => {
    const raw = (rootFilesQuery.data ?? []).filter((n) => n.mimeType !== MIME_FOLDER);
    const filtered = showNonDocFiles ? raw : raw.filter((n) => n.mimeType === MIME_DOC);
    return sortNodes(filtered, sortOrderMap);
  }, [rootFilesQuery.data, showNonDocFiles, sortOrderMap]);

  // Items at the drive-root share one SortableContext so reorders at
  // this level stay independent from reorders inside any subfolder.
  const rootItemIds = useMemo(
    () => [...rootFolders.map((n) => n.id), ...rootFiles.map((n) => n.id)],
    [rootFolders, rootFiles],
  );

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
                items={rootItemIds}
                strategy={verticalListSortingStrategy}
              >
                {rootFolders.map((folder) => (
                  <SortableItem key={folder.id} id={folder.id} variant="item">
                    <TreeNode
                      node={folder}
                      driveId={drive.id}
                      parentFolderId={null}
                      childrenByParent={childrenByParent}
                      sortOrderMap={sortOrderMap}
                      activeDriveId={activeDriveId}
                      showNonDocFiles={showNonDocFiles}
                      folderIconOverride={folderIconMap.get(folder.id) ?? null}
                      iconOverrides={folderIconMap}
                      docIconOverrides={docIconOverrides}
                      depth={0}
                    />
                  </SortableItem>
                ))}
                {rootFiles.map((file) => (
                  <SortableItem key={file.id} id={file.id} variant="item">
                    <FileRow
                      node={file}
                      driveId={drive.id}
                      parentFolderId={null}
                      activeDriveId={activeDriveId}
                      iconOverride={docIconOverrides.get(file.id) ?? null}
                      depth={0}
                    />
                  </SortableItem>
                ))}
              </SortableContext>
            )}
            {rootFilesQuery.isLoading && (
              <SidebarMenuItem>
                <span className="text-muted-foreground px-2 py-1 text-xs">Loading files…</span>
              </SidebarMenuItem>
            )}
            {!treeQuery.isLoading &&
              rootFolders.length === 0 &&
              rootFiles.length === 0 &&
              !rootFilesQuery.isLoading && (
                <SidebarMenuItem>
                  <span className="text-muted-foreground px-2 py-1 text-xs">Empty drive</span>
                </SidebarMenuItem>
              )}
          </SidebarMenu>
        </SidebarGroupContent>
      )}
    </SidebarGroup>
  );
}
