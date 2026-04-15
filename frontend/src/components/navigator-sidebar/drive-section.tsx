"use client";

import { useMemo, useState } from "react";
import { ChevronRight, HardDrive } from "lucide-react";
import {
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarMenu,
  SidebarMenuItem,
  SidebarMenuSub,
  SidebarMenuSubItem,
} from "@/components/ui/sidebar";
import { cn } from "@/lib/utils";
import { useDriveTree, useFolderFiles } from "@/hooks/use-navigator-tree";
import type { DriveNode, DriveSummary, FolderIcon, SortOrder } from "@/types/navigator";
import { MIME_DOC, MIME_FOLDER } from "@/types/navigator";
import { TreeNode } from "./tree-node";
import { FileRow } from "./file-row";
import { CreateFileMenu } from "./create-file-menu";

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
  const [expanded, setExpanded] = useState(true);
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

  // Doc icon overrides are intentionally empty in D2. D4/D5 will wire
  // them through — until then files fall back to their static type icon.
  const docIconOverrides = useMemo(() => new Map<string, string | null>(), []);

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

  return (
    <SidebarGroup>
      <div className="group/driveheader relative">
        <SidebarGroupLabel asChild>
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
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
            {!treeQuery.isLoading &&
              rootFolders.map((folder) => (
                <SidebarMenuItem key={folder.id} className="group/navitem relative">
                  <TreeNode
                    node={folder}
                    driveId={drive.id}
                    childrenByParent={childrenByParent}
                    activeDriveId={activeDriveId}
                    showNonDocFiles={showNonDocFiles}
                    folderIconOverride={folderIconMap.get(folder.id) ?? null}
                    iconOverrides={folderIconMap}
                    docIconOverrides={docIconOverrides}
                    depth={0}
                  />
                </SidebarMenuItem>
              ))}
            {rootFilesQuery.isLoading && (
              <SidebarMenuItem>
                <span className="text-muted-foreground px-2 py-1 text-xs">Loading files…</span>
              </SidebarMenuItem>
            )}
            {rootFiles.map((file) => (
              <SidebarMenuSub key={file.id}>
                <SidebarMenuSubItem className="group/navitem relative">
                  <FileRow
                    node={file}
                    driveId={drive.id}
                    activeDriveId={activeDriveId}
                    iconOverride={docIconOverrides.get(file.id) ?? null}
                  />
                </SidebarMenuSubItem>
              </SidebarMenuSub>
            ))}
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
