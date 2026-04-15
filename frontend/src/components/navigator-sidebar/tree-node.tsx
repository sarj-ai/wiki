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

interface TreeNodeProps {
  node: DriveNode;
  childrenByParent: Map<string, DriveNode[]>;
  activeDriveId: string | null;
  showNonDocFiles: boolean;
  folderIconOverride?: string | null;
  iconOverrides: Map<string, string | null>;
  docIconOverrides: Map<string, string | null>;
  depth: number;
}

export function TreeNode({
  node,
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
        className="group/folder"
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
      {expanded && (
        <SidebarMenuSub>
          {subFolders.map((child) => (
            <SidebarMenuSubItem key={child.id}>
              <TreeNode
                node={child}
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
            <SidebarMenuSubItem key={file.id}>
              <FileRow
                node={file}
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
