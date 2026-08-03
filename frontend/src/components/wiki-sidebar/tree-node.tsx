"use client";

import { SortableContext, verticalListSortingStrategy } from "@dnd-kit/sortable";
import { useQueryClient } from "@tanstack/react-query";
import { ChevronRight, Loader2 } from "lucide-react";
import { memo, type ReactNode } from "react";
import { useDebouncedCallback } from "use-debounce";

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
import type { DriveNode } from "@/types/wiki";
import { MIME_DOC, MIME_FOLDER, MIME_HTML } from "@/types/wiki";

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
  depth: number;
  node: DriveNode;
  parentFolderId: string | undefined;
}

const HOVER_PREFETCH_DELAY_MS = 80;

interface ShortcutMeta {
  expandFolderId: null | string;
  inaccessible: boolean;
  isFolder: boolean;
  isFolderShortcut: boolean;
  isShortcut: boolean;
  tooltip: null | string;
}

interface DriveSectionCtxShape {
  childrenByParent: Map<string, DriveNode[]>;
  filesByParent: Map<string, DriveNode[]>;
  optimisticByContainer: Map<string, readonly string[]>;
  showNonDocFiles: boolean;
  sortOrderMap: Map<string, number>;
  treeFilesComplete: boolean;
}

interface FolderHeaderProps {
  actions: ReactNode;
  depth: number;
  expanded: boolean;
  iconEmoji: string | undefined;
  isDropIntoFolder: boolean;
  isEmpty: boolean;
  isMoving: boolean;
  itemCount: number | undefined;
  meta: ShortcutMeta;
  node: DriveNode;
  onHoverEnd: () => void;
  onHoverStart: () => void;
  onIconChange: (next: null | string) => Promise<void>;
  onToggle: () => void;
}

function resolveShortcutMeta(node: DriveNode): ShortcutMeta {
  const s = resolveShortcutTarget(node);
  return {
    expandFolderId: s.isFolderShortcut ? (node.shortcutTargetId ?? null) : node.id,
    inaccessible: s.inaccessible,
    isFolder: node.mimeType === MIME_FOLDER || s.isFolderShortcut,
    isFolderShortcut: s.isFolderShortcut,
    isShortcut: s.isShortcut,
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

function TreeNodeImpl({ depth, node, parentFolderId }: TreeNodeProps) {
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
      queryFn: async () => wikiApi.getFolderFiles(folderId),
      queryKey: wikiKeys.folderFiles(folderId),
    });
  }, HOVER_PREFETCH_DELAY_MS);

  const setFolderIcon = useSetFolderIcon();

  const isDropIntoFolder = useIsDropIntoFolder(node.id);

  const isMoving = useIsMovingFile(node.id);

  if (!meta.isFolder) {
    return (
      <FileRow
        activeDriveId={ctx.activeDriveId}
        depth={depth}
        driveId={ctx.driveId}
        iconOverride={ctx.docIconOverrides.get(node.id)}
        node={node}
        parentFolderId={parentFolderId}
      />
    );
  }

  const childItems = buildChildItems(node, meta, ctx, lazyFiles ?? []);
  const sortableItems = childItems.map((c) => c.node.id);
  const iconEmoji = ctx.iconOverrides.get(node.id);
  const itemCount = meta.isFolderShortcut || !ctx.treeFilesComplete ? undefined : childItems.length;
  const isEmpty = itemCount === 0;

  const handleFolderIconChange = async (next: null | string) => {
    await runWithErrorToast(async () => {
      await setFolderIcon.mutateAsync({ folderDriveId: node.id, icon: next });
    }, "Failed to update icon");
  };

  return (
    <>
      <FolderHeader
        actions={
          <RowActions>
            <CreateFileMenu driveId={ctx.driveId} parentFolderId={node.id} />
            <ItemMenu
              currentName={node.name}
              driveId={ctx.driveId}
              fileId={node.id}
              isFolder
              parentFolderId={parentFolderId}
            />
          </RowActions>
        }
        depth={depth}
        expanded={expanded}
        iconEmoji={iconEmoji}
        isDropIntoFolder={isDropIntoFolder}
        isEmpty={isEmpty}
        isMoving={isMoving}
        itemCount={itemCount}
        meta={meta}
        node={node}
        onHoverEnd={() => {
          prefetchChildren.cancel();
        }}
        onHoverStart={prefetchChildren}
        onIconChange={handleFolderIconChange}
        onToggle={() => {
          if (meta.inaccessible || isEmpty) return;
          setExpanded(!expanded);
        }}
      />
      {expanded ? (
        <SidebarMenuSub className="mx-0 gap-0.5 border-l-0 px-0">
          <SortableContext
            id={`parent:${node.id}`}
            items={sortableItems}
            strategy={verticalListSortingStrategy}
          >
            {childItems.map(({ node: child, type }) => (
              <SortableItem
                data={{ isFolder: type === "folder" }}
                id={child.id}
                key={child.id}
                variant="subitem"
              >
                {type === "folder" ? (
                  <TreeNode depth={depth + 1} node={child} parentFolderId={node.id} />
                ) : (
                  <FileRow
                    activeDriveId={ctx.activeDriveId}
                    depth={depth + 1}
                    driveId={ctx.driveId}
                    iconOverride={ctx.docIconOverrides.get(child.id)}
                    node={child}
                    parentFolderId={node.id}
                  />
                )}
              </SortableItem>
            ))}
          </SortableContext>
          {filesLoading ? (
            <SidebarMenuSubItem>
              <span className="text-muted-foreground px-2 py-1 text-xs">Loading files…</span>
            </SidebarMenuSubItem>
          ) : null}
          {!filesLoading && childItems.length === 0 && (
            <EmptyDropZone label="Empty" parentId={node.id} variant="subitem" />
          )}
        </SidebarMenuSub>
      ) : null}
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
    : rawFiles.filter((n) => n.mimeType === MIME_DOC || n.mimeType === MIME_HTML);
  const folderChildren = meta.isFolderShortcut
    ? lazyAll.filter(isFolderLikeNode)
    : (ctx.childrenByParent.get(node.id) ?? []);

  const raw: KindedNode[] = [
    ...folderChildren.map((n): KindedNode => ({ node: n, type: "folder" })),
    ...filteredFiles.map((n): KindedNode => ({ node: n, type: "file" })),
  ];

  const optimisticOrder = ctx.optimisticByContainer.get(`parent:${node.id}`) ?? null;
  const sorted = optimisticOrder
    ? raw
    : [...raw].toSorted((a, b) => compareNodeOrder(a, b, ctx.sortOrderMap));
  return applyOptimisticOrder(sorted, optimisticOrder);
}

function FolderHeader({
  actions,
  depth,
  expanded,
  iconEmoji,
  isDropIntoFolder,
  isEmpty,
  isMoving,
  itemCount,
  meta,
  node,
  onHoverEnd,
  onHoverStart,
  onIconChange,
  onToggle,
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
        aria-expanded={meta.inaccessible || isEmpty ? undefined : expanded}
        className={cn(
          "pr-14 transition-[background-color,box-shadow] duration-150",

          isDropIntoFolder && "ring-primary/60 bg-primary/10 ring-1",
          isMoving && "opacity-60",
          meta.inaccessible && "text-muted-foreground/60 cursor-not-allowed",
          isEmpty && "text-muted-foreground/60 cursor-default",
        )}
        data-file-id={node.id}
        disabled={meta.inaccessible || isEmpty}
        onClick={onToggle}
        onPointerEnter={onHoverStart}
        onPointerLeave={onHoverEnd}
        style={{ paddingLeft: chevronPadding }}
        title={meta.tooltip ?? (isEmpty ? `${node.name} — empty` : node.name)}
      >
        <ChevronRight
          className={cn(
            "size-3.5 shrink-0 transition-transform",
            expanded && "rotate-90",
            meta.inaccessible && "opacity-30",
            isEmpty && "invisible",
          )}
        />
        <span aria-hidden="true" className="shrink-0" style={{ width: ICON_COLUMN_WIDTH - 4 }} />
        <span className="truncate">{node.name}</span>
        {itemCount !== undefined && itemCount > 0 && (
          <span className="text-muted-foreground/70 ml-auto shrink-0 pl-1 text-[10px] tabular-nums group-hover/navitem:opacity-0">
            {itemCount}
          </span>
        )}
      </SidebarMenuButton>

      <div className="absolute top-1/2 z-10 -translate-y-1/2" style={{ left: iconLeft }}>
        <PickableIcon
          ariaLabel="Change folder icon"
          dimmed={meta.inaccessible}
          onChange={onIconChange}
          pickable={!meta.isShortcut && !meta.inaccessible}
          value={iconEmoji}
        >
          {iconContent}
        </PickableIcon>
      </div>

      {!meta.isShortcut && !meta.inaccessible && actions}
      {isMoving ? (
        <div className="text-muted-foreground pointer-events-none absolute top-1/2 right-2 -translate-y-1/2">
          <Loader2 className="size-3 animate-spin" />
        </div>
      ) : null}
    </div>
  );
}

export const TreeNode = memo(TreeNodeImpl);
