"use client";

import { ChevronRight } from "lucide-react";
import { useState } from "react";
import { SidebarMenuButton, SidebarMenuSub, SidebarMenuSubItem } from "@/components/ui/sidebar";
import { renderMimeIcon } from "@/lib/file-icons";
import { cn } from "@/lib/utils";
import type { DriveNode } from "@/types/navigator";
import { MIME_FOLDER } from "@/types/navigator";
import { useFolderFiles } from "@/hooks/use-navigator-tree";
import { FileRow } from "./file-row";
import { CreateFileMenu } from "./create-file-menu";
import { ItemMenu } from "./item-menu";

interface TreeNodeProps {
  node: DriveNode;
  driveId: string;
  childrenByParent: Map<string, DriveNode[]>;
  activeDriveId: string | null;
  showNonDocFiles: boolean;
  folderIconOverride?: string | null;
  iconOverrides: Map<string, string | null>;
  docIconOverrides: Map<string, string | null>;
  depth: number;
}

// Overlay containing the hover-revealed "+" and "..." actions for a
// folder row. Sits absolutely-positioned on top of the SidebarMenuButton
// so it doesn't steal layout from the folder label.
function RowActions({ children }: { children: React.ReactNode }) {
  return (
    <div className="absolute top-1 right-1 flex items-center gap-0.5 opacity-0 group-hover/navitem:opacity-100 focus-within:opacity-100">
      {children}
    </div>
  );
}

export function TreeNode({
  node,
  driveId,
  childrenByParent,
  activeDriveId,
  showNonDocFiles,
  folderIconOverride,
  iconOverrides,
  docIconOverrides,
  depth,
}: TreeNodeProps) {
  const isFolder = node.mimeType === MIME_FOLDER;
  const [expanded, setExpanded] = useState(false);
  // Only call the lazy file query once the user expands; the hook reads
  // from the React Query cache on subsequent expands so flicker is ~0.
  const { data: lazyFiles, isLoading: filesLoading } = useFolderFiles(
    isFolder ? node.id : null,
    expanded,
  );

  if (!isFolder) {
    return (
      <FileRow
        node={node}
        driveId={driveId}
        activeDriveId={activeDriveId}
        iconOverride={docIconOverrides.get(node.id) ?? null}
      />
    );
  }

  const subFolders = childrenByParent.get(node.id) ?? [];
  // Drop subfolders from lazyFiles to avoid duplication — the tree walker
  // already covers folders. Files come only from the lazy call.
  const lazyOnlyFiles = (lazyFiles ?? []).filter((n) => n.mimeType !== MIME_FOLDER);
  const visibleFiles = showNonDocFiles
    ? lazyOnlyFiles
    : lazyOnlyFiles.filter((n) => n.mimeType === "application/vnd.google-apps.document");

  const iconEmoji = folderIconOverride ?? null;

  return (
    <>
      <SidebarMenuButton
        onClick={() => setExpanded((v) => !v)}
        className="pr-14"
        aria-expanded={expanded}
      >
        <ChevronRight
          className={cn("size-3.5 shrink-0 transition-transform", expanded && "rotate-90")}
        />
        {iconEmoji ? (
          <span className="text-base leading-none">{iconEmoji}</span>
        ) : (
          renderMimeIcon(node.mimeType, "size-4 shrink-0")
        )}
        <span className="truncate">{node.name}</span>
      </SidebarMenuButton>
      <RowActions>
        <CreateFileMenu parentFolderId={node.id} />
        <ItemMenu fileId={node.id} driveId={driveId} currentName={node.name} isFolder />
      </RowActions>
      {expanded && (
        <SidebarMenuSub>
          {subFolders.map((child) => (
            <SidebarMenuSubItem key={child.id} className="group/navitem relative">
              <TreeNode
                node={child}
                driveId={driveId}
                childrenByParent={childrenByParent}
                activeDriveId={activeDriveId}
                showNonDocFiles={showNonDocFiles}
                folderIconOverride={iconOverrides.get(child.id) ?? null}
                iconOverrides={iconOverrides}
                docIconOverrides={docIconOverrides}
                depth={depth + 1}
              />
            </SidebarMenuSubItem>
          ))}
          {filesLoading && (
            <SidebarMenuSubItem>
              <span className="text-muted-foreground px-2 py-1 text-xs">Loading files…</span>
            </SidebarMenuSubItem>
          )}
          {visibleFiles.map((file) => (
            <SidebarMenuSubItem key={file.id} className="group/navitem relative">
              <FileRow
                node={file}
                driveId={driveId}
                activeDriveId={activeDriveId}
                iconOverride={docIconOverrides.get(file.id) ?? null}
              />
            </SidebarMenuSubItem>
          ))}
          {!filesLoading && subFolders.length === 0 && visibleFiles.length === 0 && (
            <SidebarMenuSubItem>
              <span className="text-muted-foreground px-2 py-1 text-xs">Empty</span>
            </SidebarMenuSubItem>
          )}
        </SidebarMenuSub>
      )}
    </>
  );
}
