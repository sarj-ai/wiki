"use client";

import { ChevronRight } from "lucide-react";
import { SortableContext, verticalListSortingStrategy } from "@dnd-kit/sortable";
import { toast } from "sonner";
import { SidebarMenuButton, SidebarMenuSub, SidebarMenuSubItem } from "@/components/ui/sidebar";
import { renderMimeIcon } from "@/lib/file-icons";
import { cn } from "@/lib/utils";
import type { DriveNode } from "@/types/navigator";
import { MIME_FOLDER } from "@/types/navigator";
import { useFolderFiles } from "@/hooks/use-navigator-tree";
import { useExpandedFolder } from "@/hooks/use-expanded-folders";
import { useSetFolderIcon } from "@/hooks/use-navigator-mutations";
import { FileRow } from "./file-row";
import { CreateFileMenu } from "./create-file-menu";
import { EmojiPickerPopover } from "./emoji-picker-popover";
import { ItemMenu } from "./item-menu";
import { SortableItem } from "./sortable-item";

interface TreeNodeProps {
  node: DriveNode;
  driveId: string;
  parentFolderId: string | null;
  childrenByParent: Map<string, DriveNode[]>;
  sortOrderMap: Map<string, number>;
  activeDriveId: string | null;
  showNonDocFiles: boolean;
  folderIconOverride?: string | null;
  iconOverrides: Map<string, string | null>;
  docIconOverrides: Map<string, string | null>;
  depth: number;
}

// Width reserved for the absolute-positioned icon column. Kept in
// sync with file-row.tsx so file and folder rows share the exact
// same horizontal rhythm at every depth.
const ICON_COLUMN_WIDTH = 24;

function sortNodes(nodes: DriveNode[], sortOrderMap: Map<string, number>): DriveNode[] {
  return [...nodes].sort((a, b) => {
    const aOverride = sortOrderMap.get(a.id);
    const bOverride = sortOrderMap.get(b.id);
    if (aOverride !== undefined && bOverride !== undefined) return aOverride - bOverride;
    if (aOverride !== undefined) return -1;
    if (bOverride !== undefined) return 1;
    return a.name.localeCompare(b.name);
  });
}

// Overlay containing the hover-revealed "+" and "..." actions for a
// folder row. Vertically centered so the ``...`` button lines up with
// the icon and name instead of clinging to the top edge of the row.
function RowActions({ children }: { children: React.ReactNode }) {
  return (
    <div className="absolute top-1/2 right-1 z-20 flex -translate-y-1/2 items-center gap-0.5 opacity-0 group-hover/navitem:opacity-100 focus-within:opacity-100">
      {children}
    </div>
  );
}

export function TreeNode({
  node,
  driveId,
  parentFolderId,
  childrenByParent,
  sortOrderMap,
  activeDriveId,
  showNonDocFiles,
  folderIconOverride,
  iconOverrides,
  docIconOverrides,
  depth,
}: TreeNodeProps) {
  const isFolder = node.mimeType === MIME_FOLDER;
  const [expanded, setExpanded] = useExpandedFolder(node.id);
  // Only call the lazy file query once the user expands; the hook reads
  // from the React Query cache on subsequent expands so flicker is ~0.
  const { data: lazyFiles, isLoading: filesLoading } = useFolderFiles(
    isFolder ? node.id : null,
    expanded,
  );
  // Icon mutation lives at the top of the component so the hook
  // order is stable across folder vs non-folder branches below.
  const setFolderIcon = useSetFolderIcon();

  if (!isFolder) {
    return (
      <FileRow
        node={node}
        driveId={driveId}
        parentFolderId={parentFolderId}
        activeDriveId={activeDriveId}
        iconOverride={docIconOverrides.get(node.id) ?? null}
        depth={depth}
      />
    );
  }

  const subFolders = sortNodes(childrenByParent.get(node.id) ?? [], sortOrderMap);
  // Drop subfolders from lazyFiles to avoid duplication — the tree walker
  // already covers folders. Files come only from the lazy call.
  const lazyOnlyFiles = (lazyFiles ?? []).filter((n) => n.mimeType !== MIME_FOLDER);
  const filteredFiles = showNonDocFiles
    ? lazyOnlyFiles
    : lazyOnlyFiles.filter((n) => n.mimeType === "application/vnd.google-apps.document");
  const visibleFiles = sortNodes(filteredFiles, sortOrderMap);

  // Each expanded folder is its own SortableContext so within-folder
  // drag-drop reorder works without tripping on siblings in other
  // parents. containerId matches our "parent:..." convention.
  const sortableItems = [...subFolders.map((n) => n.id), ...visibleFiles.map((n) => n.id)];
  const iconEmoji = folderIconOverride ?? null;

  const handleFolderIconChange = async (next: string | null) => {
    try {
      await setFolderIcon.mutateAsync({ folderDriveId: node.id, icon: next });
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to update icon";
      toast.error(message);
    }
  };

  const folderIconContent = iconEmoji ? (
    <span className="text-base leading-none">{iconEmoji}</span>
  ) : (
    renderMimeIcon(node.mimeType, "size-4 shrink-0")
  );

  // Layout mirrors file-row.tsx: the chevron stays inside the expand
  // button, but the folder icon is pulled out into an absolutely-
  // positioned overlay. This guarantees that clicking the icon can
  // never trigger the button's expand/collapse handler via event
  // bubbling — the icon click lives in a different subtree entirely,
  // so radix Popover's toggle runs cleanly.
  const chevronPadding = `${depth * 12 + 8}px`;
  const iconLeft = `${depth * 12 + 8 + 18}px`; // chevron width (~14) + gap

  return (
    <>
      <SidebarMenuButton
        onClick={() => setExpanded(!expanded)}
        className="pr-14"
        style={{ paddingLeft: chevronPadding }}
        aria-expanded={expanded}
        title={node.name}
      >
        <ChevronRight
          className={cn("size-3.5 shrink-0 transition-transform", expanded && "rotate-90")}
        />
        <span className="shrink-0" style={{ width: ICON_COLUMN_WIDTH - 4 }} aria-hidden="true" />
        <span className="truncate">{node.name}</span>
      </SidebarMenuButton>
      <div className="absolute top-1/2 z-10 -translate-y-1/2" style={{ left: iconLeft }}>
        <EmojiPickerPopover value={iconEmoji} onChange={handleFolderIconChange}>
          <button
            type="button"
            aria-label="Change folder icon"
            // Matches the file-row icon: hover uses the full sidebar
            // highlight tokens so folder and file icons share the
            // same pickable affordance and automatically flip with
            // the theme.
            className="hover:bg-sidebar-accent hover:text-sidebar-accent-foreground flex size-6 shrink-0 cursor-pointer items-center justify-center rounded-md transition-colors"
          >
            {folderIconContent}
          </button>
        </EmojiPickerPopover>
      </div>
      <RowActions>
        <CreateFileMenu parentFolderId={node.id} />
        <ItemMenu
          fileId={node.id}
          driveId={driveId}
          parentFolderId={parentFolderId}
          currentName={node.name}
          isFolder
        />
      </RowActions>
      {expanded && (
        // Flatten the nested sub so recursion doesn't compound margins
        // across levels — depth indentation lives on the row content
        // itself via paddingLeft so 8+ levels still have room to breathe.
        <SidebarMenuSub className="mx-0 gap-0.5 border-l-0 px-0">
          <SortableContext
            id={`parent:${node.id}`}
            items={sortableItems}
            strategy={verticalListSortingStrategy}
          >
            {subFolders.map((child) => (
              <SortableItem key={child.id} id={child.id} variant="subitem">
                <TreeNode
                  node={child}
                  driveId={driveId}
                  parentFolderId={node.id}
                  childrenByParent={childrenByParent}
                  sortOrderMap={sortOrderMap}
                  activeDriveId={activeDriveId}
                  showNonDocFiles={showNonDocFiles}
                  folderIconOverride={iconOverrides.get(child.id) ?? null}
                  iconOverrides={iconOverrides}
                  docIconOverrides={docIconOverrides}
                  depth={depth + 1}
                />
              </SortableItem>
            ))}
            {visibleFiles.map((file) => (
              <SortableItem key={file.id} id={file.id} variant="subitem">
                <FileRow
                  node={file}
                  driveId={driveId}
                  parentFolderId={node.id}
                  activeDriveId={activeDriveId}
                  iconOverride={docIconOverrides.get(file.id) ?? null}
                  depth={depth + 1}
                />
              </SortableItem>
            ))}
          </SortableContext>
          {filesLoading && (
            <SidebarMenuSubItem>
              <span className="text-muted-foreground px-2 py-1 text-xs">Loading files…</span>
            </SidebarMenuSubItem>
          )}
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
