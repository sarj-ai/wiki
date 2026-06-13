"use client";

import { SortableContext, verticalListSortingStrategy } from "@dnd-kit/sortable";
import { useQueryClient } from "@tanstack/react-query";
import { ChevronRight, Loader2 } from "lucide-react";
import { memo, type ReactNode } from "react";
import { useDebouncedCallback } from "use-debounce";

import type { DriveNode } from "@/types/wiki";

import { SidebarMenuButton, SidebarMenuSub, SidebarMenuSubItem } from "@/components/ui/sidebar";
import { useExpandedFolder } from "@/hooks/use-expanded-folders";
import { useSetFolderIcon } from "@/hooks/use-wiki-mutations";
import { useFolderFiles } from "@/hooks/use-wiki-tree";
import { renderNodeIcon } from "@/lib/file-icons";
import { runWithErrorToast } from "@/lib/mutation-toast";
import { resolveShortcutTarget } from "@/lib/shortcut-resolver";
import { cn } from "@/lib/utils";
import { wikiApi } from "@/lib/wiki-api";
import { wikiKeys } from "@/lib/wiki-keys";
import {
  applyOptimisticOrder,
  compareNodeOrder,
  isFolderLikeNode,
  type KindedNode,
} from "@/lib/wiki-tree";
import { MIME_DOC, MIME_FOLDER } from "@/types/wiki";

import { CreateFileMenu } from "./create-file-menu";
import { useDriveSectionContext } from "./drive-context";
import { EmptyDropZone } from "./empty-droppable";
import { FileRow } from "./file-row";
import { ItemMenu } from "./item-menu";
import { CHEVRON_OFFSET, ICON_COLUMN_WIDTH, rowIndent } from "./layout-constants";
import { PickableIcon } from "./pickable-icon";
import { SortableItem } from "./sortable-item";
import { useIsDropIntoFolder } from "./use-drag-over-store";
import { useIsMovingFile } from "./use-in-flight-moves";

interface TreeNodeProps {
  node: DriveNode;
  parentFolderId: string | undefined;
  depth: number;
}

const HOVER_PREFETCH_DELAY_MS = 80;

interface ShortcutMeta {
  isShortcut: boolean;
  isFolderShortcut: boolean;
  isFolder: boolean;
  inaccessible: boolean;
  expandFolderId: string | null;
  tooltip: string | null;
}

interface DriveSectionCtxShape {
  childrenByParent: Map<string, DriveNode[]>;
  filesByParent: Map<string, DriveNode[]>;
  treeFilesComplete: boolean;
  showNonDocFiles: boolean;
  sortOrderMap: Map<string, number>;
  optimisticByContainer: Map<string, readonly string[]>;
}

interface FolderHeaderProps {
  node: DriveNode;
  meta: ShortcutMeta;
  depth: number;
  expanded: boolean;
  onToggle: () => void;
  isDropIntoFolder: boolean;
  isMoving: boolean;
  iconEmoji: string | undefined;
  itemCount: number | undefined;
  isEmpty: boolean;
  onIconChange: (next: string | null) => Promise<void>;
  onHoverStart: () => void;
  onHoverEnd: () => void;
  actions: ReactNode;
}

function resolveShortcutMeta(node: DriveNode): ShortcutMeta {
  const s = resolveShortcutTarget(node);
  return {
    isShortcut: s.isShortcut,
    isFolderShortcut: s.isFolderShortcut,
    isFolder: node.mimeType === MIME_FOLDER || s.isFolderShortcut,
    inaccessible: s.inaccessible,
    expandFolderId: s.isFolderShortcut ? (node.shortcutTargetId ?? null) : node.id,
    tooltip: s.tooltip,
  };
}

function RowActions({ children }: { children: ReactNode }) {
  return (
    <div className="absolute top-1/2 right-1 z-20 flex -translate-y-1/2 items-center gap-0.5 opacity-0 group-hover/navitem:opacity-100 focus-within:opacity-100">
      {children}
    </div>
  );
}

function TreeNodeImpl({ node, parentFolderId, depth }: TreeNodeProps) {
  const ctx = useDriveSectionContext();
  const meta = resolveShortcutMeta(node);
  const [expanded, setExpanded] = useExpandedFolder(node.id);
  const needsLazyFiles = meta.isFolderShortcut || !ctx.treeFilesComplete;
  const { data: lazyFiles, isLoading: filesLoading } = useFolderFiles(
    meta.isFolder && needsLazyFiles && !meta.inaccessible ? meta.expandFolderId : null,
    expanded,
  );
  const queryClient = useQueryClient();
  const prefetchChildren = useDebouncedCallback(() => {
    const folderId = meta.expandFolderId;
    if (!needsLazyFiles || expanded || meta.inaccessible || !folderId) return;
    void queryClient.prefetchQuery({
      queryKey: wikiKeys.folderFiles(folderId),
      queryFn: () => wikiApi.getFolderFiles(folderId),
    });
  }, HOVER_PREFETCH_DELAY_MS);

  const setFolderIcon = useSetFolderIcon();

  const isDropIntoFolder = useIsDropIntoFolder(node.id);

  const isMoving = useIsMovingFile(node.id);

  if (!meta.isFolder) {
    return (
      <FileRow
        node={node}
        driveId={ctx.driveId}
        parentFolderId={parentFolderId}
        activeDriveId={ctx.activeDriveId}
        iconOverride={ctx.docIconOverrides.get(node.id)}
        depth={depth}
      />
    );
  }

  const childItems = buildChildItems(node, meta, ctx, lazyFiles ?? []);
  const sortableItems = childItems.map((c) => c.node.id);
  const iconEmoji = ctx.iconOverrides.get(node.id);
  const itemCount = meta.isFolderShortcut || !ctx.treeFilesComplete ? undefined : childItems.length;
  const isEmpty = itemCount === 0;

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
          if (meta.inaccessible || isEmpty) return;
          setExpanded(!expanded);
        }}
        isDropIntoFolder={isDropIntoFolder}
        isMoving={isMoving}
        iconEmoji={iconEmoji}
        itemCount={itemCount}
        isEmpty={isEmpty}
        onIconChange={handleFolderIconChange}
        onHoverStart={prefetchChildren}
        onHoverEnd={() => {
          prefetchChildren.cancel();
        }}
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
                    iconOverride={ctx.docIconOverrides.get(child.id)}
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

function buildChildItems(
  node: DriveNode,
  meta: ShortcutMeta,
  ctx: DriveSectionCtxShape,
  lazyAll: DriveNode[],
): KindedNode[] {
  const rawFiles =
    meta.isFolderShortcut || !ctx.treeFilesComplete
      ? lazyAll.filter((n) => !isFolderLikeNode(n))
      : (ctx.filesByParent.get(node.id) ?? []);
  const filteredFiles = ctx.showNonDocFiles
    ? rawFiles
    : rawFiles.filter((n) => n.mimeType === MIME_DOC);
  const folderChildren = meta.isFolderShortcut
    ? lazyAll.filter(isFolderLikeNode)
    : (ctx.childrenByParent.get(node.id) ?? []);

  const raw: KindedNode[] = [
    ...folderChildren.map((n): KindedNode => ({ type: "folder", node: n })),
    ...filteredFiles.map((n): KindedNode => ({ type: "file", node: n })),
  ];

  const optimisticOrder = ctx.optimisticByContainer.get(`parent:${node.id}`) ?? null;
  const sorted = optimisticOrder
    ? raw
    : [...raw].sort((a, b) => compareNodeOrder(a, b, ctx.sortOrderMap));
  return applyOptimisticOrder(sorted, optimisticOrder);
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
  itemCount,
  isEmpty,
  onIconChange,
  onHoverStart,
  onHoverEnd,
  actions,
}: FolderHeaderProps) {
  const chevronPadding = `${rowIndent(depth)}px`;
  const iconLeft = `${rowIndent(depth) + CHEVRON_OFFSET}px`;
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
        onPointerEnter={onHoverStart}
        onPointerLeave={onHoverEnd}
        disabled={meta.inaccessible || isEmpty}
        className={cn(
          "pr-14 transition-[background-color,box-shadow] duration-150",

          isDropIntoFolder && "ring-primary/60 bg-primary/10 ring-1",
          isMoving && "opacity-60",
          meta.inaccessible && "text-muted-foreground/60 cursor-not-allowed",
          isEmpty && "text-muted-foreground/60 cursor-default",
        )}
        style={{ paddingLeft: chevronPadding }}
        aria-expanded={meta.inaccessible || isEmpty ? undefined : expanded}
        title={meta.tooltip ?? (isEmpty ? `${node.name} — empty` : node.name)}
        data-file-id={node.id}
      >
        <ChevronRight
          className={cn(
            "size-3.5 shrink-0 transition-transform",
            expanded && "rotate-90",
            meta.inaccessible && "opacity-30",
            isEmpty && "invisible",
          )}
        />
        <span className="shrink-0" style={{ width: ICON_COLUMN_WIDTH - 4 }} aria-hidden="true" />
        <span className="truncate">{node.name}</span>
        {itemCount !== undefined && itemCount > 0 && (
          <span className="text-muted-foreground/70 ml-auto shrink-0 pl-1 text-[10px] tabular-nums group-hover/navitem:opacity-0">
            {itemCount}
          </span>
        )}
      </SidebarMenuButton>

      <div className="absolute top-1/2 z-10 -translate-y-1/2" style={{ left: iconLeft }}>
        <PickableIcon
          pickable={!meta.isShortcut && !meta.inaccessible}
          dimmed={meta.inaccessible}
          value={iconEmoji}
          onChange={onIconChange}
          ariaLabel="Change folder icon"
        >
          {iconContent}
        </PickableIcon>
      </div>

      {!meta.isShortcut && !meta.inaccessible && actions}
      {isMoving && (
        <div className="text-muted-foreground pointer-events-none absolute top-1/2 right-2 -translate-y-1/2">
          <Loader2 className="size-3 animate-spin" />
        </div>
      )}
    </div>
  );
}

export const TreeNode = memo(TreeNodeImpl);
