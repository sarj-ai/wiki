"use client";

import { SortableContext, verticalListSortingStrategy } from "@dnd-kit/sortable";
import { ChevronRight, Loader2 } from "lucide-react";
import { memo, type ReactNode } from "react";

import type { DriveNode } from "@/types/wiki";

import { Button } from "@/components/ui/button";
import { SidebarMenuButton, SidebarMenuSub, SidebarMenuSubItem } from "@/components/ui/sidebar";
import { useExpandedFolder } from "@/hooks/use-expanded-folders";
import { useSetFolderIcon } from "@/hooks/use-wiki-mutations";
import { useFolderFiles } from "@/hooks/use-wiki-tree";
import { renderNodeIcon } from "@/lib/file-icons";
import { runWithErrorToast } from "@/lib/mutation-toast";
import { cn } from "@/lib/utils";
import {
  applyOptimisticOrder,
  compareNodeOrder,
  isFolderLikeNode,
  type KindedNode,
} from "@/lib/wiki-tree";
import { MIME_DOC, MIME_FOLDER, MIME_SHORTCUT } from "@/types/wiki";

import { CreateFileMenu } from "./create-file-menu";
import { useDriveSectionContext } from "./drive-context";
import { EmojiPickerPopover } from "./emoji-picker-popover";
import { EmptyDropZone } from "./empty-droppable";
import { FileRow } from "./file-row";
import { ItemMenu } from "./item-menu";
import { SortableItem } from "./sortable-item";
import { useIsDropIntoFolder } from "./use-drag-over-store";
import { useIsMovingFile } from "./use-in-flight-moves";

interface TreeNodeProps {
  node: DriveNode;
  parentFolderId: string | null;
  depth: number;
}

// Width reserved for the absolute-positioned icon column. Kept in
// sync with file-row.tsx so file and folder rows share the exact
// same horizontal rhythm at every depth.
const ICON_COLUMN_WIDTH = 24;

interface ShortcutMeta {
  isShortcut: boolean;
  isFolderShortcut: boolean;
  isFolder: boolean;
  inaccessible: boolean;
  expandFolderId: string | null;
  tooltip: string | null;
}

function resolveShortcutMeta(node: DriveNode): ShortcutMeta {
  const isShortcut = node.mimeType === MIME_SHORTCUT;
  const isFolderShortcut = isShortcut && node.shortcutTargetMimeType === MIME_FOLDER;
  const inaccessible = isShortcut && node.shortcutInaccessible === true;
  const tooltip = isShortcut
    ? inaccessible
      ? "You don't have access to this shortcut's target. Contact your admin."
      : node.shortcutTargetDriveName
        ? `Shortcut → ${node.shortcutTargetDriveName}`
        : "Shortcut"
    : null;
  return {
    isShortcut,
    isFolderShortcut,
    isFolder: node.mimeType === MIME_FOLDER || isFolderShortcut,
    inaccessible,
    // Folder shortcuts expand by listing the *target's* children — the
    // shortcut's own id has no children of its own in Drive.
    expandFolderId: isFolderShortcut ? (node.shortcutTargetId ?? null) : node.id,
    tooltip,
  };
}

// Overlay containing the hover-revealed "+" and "..." actions for a
// folder row. Vertically centered so the ``...`` button lines up with
// the icon and name instead of clinging to the top edge of the row.
function RowActions({ children }: { children: ReactNode }) {
  return (
    <div className="absolute top-1/2 right-1 z-20 flex -translate-y-1/2 items-center gap-0.5 opacity-0 group-hover/navitem:opacity-100 focus-within:opacity-100">
      {children}
    </div>
  );
}

export const TreeNode = memo(TreeNodeImpl);

function TreeNodeImpl({ node, parentFolderId, depth }: TreeNodeProps) {
  const ctx = useDriveSectionContext();
  const meta = resolveShortcutMeta(node);
  const [expanded, setExpanded] = useExpandedFolder(node.id);
  // Only call the lazy file query once the user expands; the hook reads
  // from the React Query cache on subsequent expands so flicker is ~0.
  // Inaccessible shortcuts never fetch — their target ACL would 403.
  const { data: lazyFiles, isLoading: filesLoading } = useFolderFiles(
    meta.isFolder && !meta.inaccessible ? meta.expandFolderId : null,
    expanded,
  );
  // Icon mutation lives at the top of the component so the hook
  // order is stable across folder vs non-folder branches below.
  const setFolderIcon = useSetFolderIcon();
  // True when the dragged row will land *inside* this folder.
  const isDropIntoFolder = useIsDropIntoFolder(node.id);
  // Spinner overlay while this folder's move is settling with Drive.
  const isMoving = useIsMovingFile(node.id);

  if (!meta.isFolder) {
    return (
      <FileRow
        node={node}
        driveId={ctx.driveId}
        parentFolderId={parentFolderId}
        activeDriveId={ctx.activeDriveId}
        iconOverride={ctx.docIconOverrides.get(node.id) ?? null}
        depth={depth}
      />
    );
  }

  const childItems = buildChildItems(node, meta, ctx, lazyFiles ?? []);
  const sortableItems = childItems.map((c) => c.node.id);
  const iconEmoji = ctx.iconOverrides.get(node.id) ?? null;

  const handleFolderIconChange = async (next: string | null) => {
    await runWithErrorToast(async () => {
      await setFolderIcon.mutateAsync({ folderDriveId: node.id, icon: next });
    }, "Failed to update icon");
  };

  return (
    <>
      <FolderHeader
        node={node}
        meta={meta}
        depth={depth}
        expanded={expanded}
        onToggle={() => {
          if (meta.inaccessible) return;
          setExpanded(!expanded);
        }}
        isDropIntoFolder={isDropIntoFolder}
        isMoving={isMoving}
        iconEmoji={iconEmoji}
        onIconChange={handleFolderIconChange}
        actions={
          <RowActions>
            <CreateFileMenu parentFolderId={node.id} />
            <ItemMenu
              fileId={node.id}
              driveId={ctx.driveId}
              parentFolderId={parentFolderId}
              currentName={node.name}
              isFolder
            />
          </RowActions>
        }
      />
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
            {childItems.map(({ type, node: child }) => (
              <SortableItem
                key={child.id}
                id={child.id}
                variant="subitem"
                data={{ isFolder: type === "folder" }}
              >
                {type === "folder" ? (
                  <TreeNode node={child} parentFolderId={node.id} depth={depth + 1} />
                ) : (
                  <FileRow
                    node={child}
                    driveId={ctx.driveId}
                    parentFolderId={node.id}
                    activeDriveId={ctx.activeDriveId}
                    iconOverride={ctx.docIconOverrides.get(child.id) ?? null}
                    depth={depth + 1}
                  />
                )}
              </SortableItem>
            ))}
          </SortableContext>
          {filesLoading && (
            <SidebarMenuSubItem>
              <span className="text-muted-foreground px-2 py-1 text-xs">Loading files…</span>
            </SidebarMenuSubItem>
          )}
          {!filesLoading && childItems.length === 0 && (
            <EmptyDropZone parentId={node.id} variant="subitem" label="Empty" />
          )}
        </SidebarMenuSub>
      )}
    </>
  );
}

interface DriveSectionCtxShape {
  childrenByParent: Map<string, DriveNode[]>;
  showNonDocFiles: boolean;
  sortOrderMap: Map<string, number>;
  optimisticByContainer: Map<string, readonly string[]>;
}

// Native folders rely on the drive walk for their subfolders and on
// the lazy ``useFolderFiles`` call for the file-level children. Folder
// shortcuts pointing into another drive don't appear in the source
// drive's walk, so we surface their subfolders from the lazy fetch
// instead — those subfolders then recurse through TreeNode and lazy-
// load their own children one level at a time.
function buildChildItems(
  node: DriveNode,
  meta: ShortcutMeta,
  ctx: DriveSectionCtxShape,
  lazyAll: DriveNode[],
): KindedNode[] {
  const lazyOnlyFiles = lazyAll.filter((n) => !isFolderLikeNode(n));
  const filteredFiles = ctx.showNonDocFiles
    ? lazyOnlyFiles
    : lazyOnlyFiles.filter((n) => n.mimeType === MIME_DOC);
  const folderChildren = meta.isFolderShortcut
    ? lazyAll.filter(isFolderLikeNode)
    : (ctx.childrenByParent.get(node.id) ?? []);

  const raw: KindedNode[] = [
    ...folderChildren.map((n): KindedNode => ({ type: "folder", node: n })),
    ...filteredFiles.map((n): KindedNode => ({ type: "file", node: n })),
  ];
  // If a drop just happened in THIS folder, the optimistic map has the
  // final order keyed by id — honour it verbatim and skip sortOrderMap,
  // which would otherwise be stale for several frames post-drop.
  const optimisticOrder = ctx.optimisticByContainer.get(`parent:${node.id}`) ?? null;
  const sorted = optimisticOrder
    ? raw
    : [...raw].sort((a, b) => compareNodeOrder(a, b, ctx.sortOrderMap));
  return applyOptimisticOrder(sorted, optimisticOrder);
}

interface FolderHeaderProps {
  node: DriveNode;
  meta: ShortcutMeta;
  depth: number;
  expanded: boolean;
  onToggle: () => void;
  isDropIntoFolder: boolean;
  isMoving: boolean;
  iconEmoji: string | null;
  onIconChange: (next: string | null) => Promise<void>;
  actions: ReactNode;
}

function FolderHeader({
  node,
  meta,
  depth,
  expanded,
  onToggle,
  isDropIntoFolder,
  isMoving,
  iconEmoji,
  onIconChange,
  actions,
}: FolderHeaderProps) {
  // Layout mirrors file-row.tsx: the chevron stays inside the expand
  // button, but the folder icon is pulled out into an absolutely-
  // positioned overlay. This guarantees that clicking the icon can
  // never trigger the button's expand/collapse handler via event
  // bubbling — the icon click lives in a different subtree entirely,
  // so radix Popover's toggle runs cleanly.
  const chevronPadding = `${depth * 12 + 8}px`;
  const iconLeft = `${depth * 12 + 8 + 18}px`; // chevron width (~14) + gap
  const iconContent =
    iconEmoji && !meta.isShortcut ? (
      <span className="text-base leading-none">{iconEmoji}</span>
    ) : (
      renderNodeIcon(node, "size-4 shrink-0")
    );

  return (
    <div className="relative">
      <SidebarMenuButton
        onClick={onToggle}
        disabled={meta.inaccessible}
        className={cn(
          "pr-14 transition-[background-color,box-shadow] duration-150",
          // Tint + ring while a drop on this row would land INSIDE the
          // folder. Distinct from the above/below drop line rendered by
          // SortableItem so the user can read the two outcomes at a
          // glance.
          isDropIntoFolder && "ring-primary/60 bg-primary/10 ring-1",
          isMoving && "opacity-60",
          meta.inaccessible && "text-muted-foreground/60 cursor-not-allowed",
        )}
        style={{ paddingLeft: chevronPadding }}
        aria-expanded={meta.inaccessible ? undefined : expanded}
        title={meta.tooltip ?? node.name}
        // Same attribute the file-reveal scroll-into-view query uses so
        // folder hits in the command palette can land on this row too.
        data-file-id={node.id}
      >
        <ChevronRight
          className={cn(
            "size-3.5 shrink-0 transition-transform",
            expanded && "rotate-90",
            meta.inaccessible && "opacity-30",
          )}
        />
        <span className="shrink-0" style={{ width: ICON_COLUMN_WIDTH - 4 }} aria-hidden="true" />
        <span className="truncate">{node.name}</span>
      </SidebarMenuButton>
      <div className="absolute top-1/2 z-10 -translate-y-1/2" style={{ left: iconLeft }}>
        <FolderIcon
          meta={meta}
          iconContent={iconContent}
          iconEmoji={iconEmoji}
          onIconChange={onIconChange}
        />
      </div>
      {/*
        Shortcut rows hide the create / rename / move actions — the
        host drive doesn't own the target, so those operations either
        don't apply (create-inside) or would mutate the wrong file
        (rename/move the *shortcut*, not the doc users see).
      */}
      {!meta.isShortcut && !meta.inaccessible && actions}
      {isMoving && (
        <div className="text-muted-foreground pointer-events-none absolute top-1/2 right-2 -translate-y-1/2">
          <Loader2 className="size-3 animate-spin" />
        </div>
      )}
    </div>
  );
}

interface FolderIconProps {
  meta: ShortcutMeta;
  iconContent: ReactNode;
  iconEmoji: string | null;
  onIconChange: (next: string | null) => Promise<void>;
}

// Folder shortcuts skip the emoji picker — the icon belongs to the
// target's home, not to the pointer. Inaccessible rows likewise:
// nothing to customise on a row the user can't open.
function FolderIcon({ meta, iconContent, iconEmoji, onIconChange }: FolderIconProps) {
  if (meta.isShortcut || meta.inaccessible) {
    return (
      <span
        className={cn(
          "flex size-6 shrink-0 items-center justify-center",
          meta.inaccessible && "opacity-50",
        )}
      >
        {iconContent}
      </span>
    );
  }
  return (
    <EmojiPickerPopover value={iconEmoji} onChange={onIconChange}>
      <Button
        variant="ghost"
        size="icon-xs"
        aria-label="Change folder icon"
        // Matches the file-row icon: hover uses the full sidebar
        // highlight tokens so folder and file icons share the same
        // pickable affordance and automatically flip with the theme.
        className="hover:bg-sidebar-accent hover:text-sidebar-accent-foreground size-6 shrink-0 cursor-pointer transition-colors"
      >
        {iconContent}
      </Button>
    </EmojiPickerPopover>
  );
}
