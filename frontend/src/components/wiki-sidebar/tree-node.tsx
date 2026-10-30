"use client";

import { SortableContext, verticalListSortingStrategy } from "@dnd-kit/sortable";
import { useQueryClient } from "@tanstack/react-query";
import { ChevronRight, Loader2 } from "lucide-react";
import { memo, type ReactNode } from "react";
import { useDebouncedCallback } from "use-debounce";

import { SidebarMenuButton, SidebarMenuSub, SidebarMenuSubItem } from "@/components/ui/sidebar";
import { useExpandedFolder } from "@/hooks/use-expanded-folders";
import { folderFilesQueryOptions, useFolderFiles } from "@/hooks/use-wiki-tree";
import { renderNodeIcon } from "@/lib/file-icons";
import { handlePrefetchError } from "@/lib/handle-prefetch-error";
import { resolveShortcutTarget } from "@/lib/shortcut-resolver";
import { allowsReusableSpeculation, readNavigatorHints } from "@/lib/speculation-policy";
import { cn } from "@/lib/utils";
import { EMPTY_NODES, indexSidebarNodes, indexVisibleDocuments } from "@/lib/wiki-indexes";
import { buildSiblingItems, type KindedNode, orderSiblingItems } from "@/lib/wiki-tree";
import type { DriveNode } from "@/types/wiki";
import { MIME_FOLDER } from "@/types/wiki";

import { CreateFileMenu } from "./create-file-menu";
import { type DriveSectionValue, useDriveSectionContext } from "./drive-context";
import { EmptyDropZone } from "./empty-drop-zone";
import { FileRow } from "./file-row";
import { ItemMenu } from "./item-menu";
import { CHEVRON_OFFSET, ICON_COLUMN_WIDTH, rowIndent } from "./layout-constants";
import { PickableIcon } from "./pickable-icon";
import { RowActions } from "./row-actions";
import { SortableItem } from "./sortable-item";
import { useIsDropIntoFolder } from "./use-drag-over-store";
import { useIsMovingFile } from "./use-in-flight-moves";

interface TreeNodeProps {
  depth: number;
  node: DriveNode;
  parentFolderId: string | undefined;
}

const HOVER_PREFETCH_DELAY_MS = 80;
const EMPTY_CHILD_ITEMS: readonly KindedNode[] = [];

interface ChildListing {
  count: number;
  items: readonly KindedNode[];
}

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
  isMoving: boolean;
  itemCount: number | undefined;
  meta: ShortcutMeta;
  node: DriveNode;
  onHoverEnd: () => void;
  onHoverStart: () => void;
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

function TreeNodeImpl({ depth, node, parentFolderId }: TreeNodeProps) {
  const ctx = useDriveSectionContext();
  const meta = resolveShortcutMeta(node);
  const [expanded, setExpanded] = useExpandedFolder(node.id);
  const needsLazyFiles = meta.isFolderShortcut || !ctx.treeFilesComplete;
  const queryClient = useQueryClient();
  const prefetchChildren = useDebouncedCallback(() => {
    const folderId = meta.expandFolderId;
    if (
      !needsLazyFiles ||
      expanded ||
      meta.inaccessible ||
      !folderId ||
      document.visibilityState !== "visible" ||
      !allowsReusableSpeculation(readNavigatorHints(navigator))
    ) {
      return;
    }
    void queryClient.query(folderFilesQueryOptions(folderId)).catch(handlePrefetchError);
  }, HOVER_PREFETCH_DELAY_MS);

  const isDropIntoFolder = useIsDropIntoFolder(node.id);

  const isMoving = useIsMovingFile(node.id);

  const {
    childrenByParent,
    filesByParent,
    optimisticByContainer,
    showNonDocFiles,
    sortOrderMap,
    treeFilesComplete,
  } = ctx;
  const itemCount =
    meta.isFolder && !meta.isFolderShortcut && treeFilesComplete
      ? buildChildItems(
          node,
          meta,
          {
            childrenByParent,
            filesByParent,
            optimisticByContainer,
            showNonDocFiles,
            sortOrderMap,
            treeFilesComplete,
          },
          EMPTY_NODES,
          false,
        ).count
      : undefined;

  if (!meta.isFolder) {
    return (
      <FileRow
        activeDriveId={ctx.activeDriveId}
        depth={depth}
        driveId={ctx.driveId}
        iconOverride={ctx.docIconOverrides.get(node.id)}
        node={node}
        parentFolderId={parentFolderId}
        pathname={ctx.pathname}
      />
    );
  }

  const iconEmoji = ctx.iconOverrides.get(node.id);

  return (
    <>
      <FolderHeader
        actions={
          <RowActions>
            <CreateFileMenu driveId={ctx.driveId} parentFolderId={node.id} parentName={node.name} />
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
        isMoving={isMoving}
        itemCount={itemCount}
        meta={meta}
        node={node}
        onHoverEnd={() => {
          prefetchChildren.cancel();
        }}
        onHoverStart={() => {
          if (needsLazyFiles && !expanded && !meta.inaccessible) prefetchChildren();
        }}
        onToggle={() => {
          if (meta.inaccessible) return;
          setExpanded(!expanded);
        }}
      />
      {expanded ? <ExpandedFolderContents ctx={ctx} depth={depth} meta={meta} node={node} /> : null}
    </>
  );
}

function ExpandedFolderContents({
  ctx,
  depth,
  meta,
  node,
}: {
  ctx: DriveSectionValue;
  depth: number;
  meta: ShortcutMeta;
  node: DriveNode;
}) {
  const needsLazyFiles = meta.isFolderShortcut || !ctx.treeFilesComplete;
  const { data: lazyFiles, isLoading: filesLoading } = useFolderFiles(
    needsLazyFiles && !meta.inaccessible ? meta.expandFolderId : null,
    true,
  );
  const { items: childItems } = buildChildItems(node, meta, ctx, lazyFiles ?? EMPTY_NODES, true);
  const sortableItems = childItems.map((child) => child.node.id);
  return (
    <SidebarMenuSub className="mx-0 gap-0.5 border-l-0 px-0">
      <SortableContext
        id={`parent:${node.id}`}
        items={sortableItems}
        strategy={verticalListSortingStrategy}
      >
        {childItems.map(({ node: child, type }) => (
          <SortableItem
            data={{
              driveId: ctx.driveId,
              isFolder: type === "folder",
              parentFolderId: node.id,
            }}
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
                pathname={ctx.pathname}
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
        <EmptyDropZone driveId={ctx.driveId} label="Empty" parentId={node.id} variant="subitem" />
      )}
    </SidebarMenuSub>
  );
}

function buildChildItems(
  node: DriveNode,
  meta: ShortcutMeta,
  ctx: DriveSectionCtxShape,
  lazyAll: readonly DriveNode[],
  expanded: boolean,
): ChildListing {
  const rawFiles =
    meta.isFolderShortcut || !ctx.treeFilesComplete
      ? indexSidebarNodes(lazyAll).files
      : (ctx.filesByParent.get(node.id) ?? EMPTY_NODES);
  const filteredFiles = ctx.showNonDocFiles ? rawFiles : indexVisibleDocuments(rawFiles);
  const folderChildren = meta.isFolderShortcut
    ? indexSidebarNodes(lazyAll).folders
    : (ctx.childrenByParent.get(node.id) ?? EMPTY_NODES);

  const count = folderChildren.length + filteredFiles.length;
  if (!expanded) return { count, items: EMPTY_CHILD_ITEMS };

  const optimisticOrder = ctx.optimisticByContainer.get(`parent:${node.id}`) ?? null;
  return {
    count,
    items: orderSiblingItems(
      buildSiblingItems(folderChildren, filteredFiles),
      ctx.sortOrderMap,
      optimisticOrder,
    ),
  };
}

function FolderHeader({
  actions,
  depth,
  expanded,
  iconEmoji,
  isDropIntoFolder,
  isMoving,
  itemCount,
  meta,
  node,
  onHoverEnd,
  onHoverStart,
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
        aria-expanded={meta.inaccessible ? undefined : expanded}
        className={cn(
          "pr-14 transition-[background-color,box-shadow] duration-150",

          isDropIntoFolder && "ring-primary/60 bg-primary/10 ring-1",
          isMoving && "opacity-60",
          meta.inaccessible && "text-muted-foreground/60 cursor-not-allowed",
        )}
        data-file-id={node.id}
        data-sidebar-row="folder"
        disabled={meta.inaccessible}
        onBlur={onHoverEnd}
        onClick={onToggle}
        onFocus={onHoverStart}
        onPointerEnter={(event) => {
          if (event.pointerType !== "touch") onHoverStart();
        }}
        onPointerLeave={onHoverEnd}
        style={{ paddingLeft: chevronPadding }}
        title={meta.tooltip ?? node.name}
      >
        <ChevronRight
          className={cn(
            "size-3.5 shrink-0 transition-transform",
            expanded && "rotate-90",
            meta.inaccessible && "opacity-30",
          )}
        />
        <span aria-hidden="true" className="shrink-0" style={{ width: ICON_COLUMN_WIDTH - 4 }} />
        <span className="truncate">{node.name}</span>
        {itemCount !== undefined && itemCount > 0 && (
          <span className="text-muted-foreground ml-auto shrink-0 pl-1 text-[10px] tabular-nums group-hover/navitem:opacity-0">
            {itemCount}
          </span>
        )}
      </SidebarMenuButton>

      <div className="absolute top-1/2 z-10 -translate-y-1/2" style={{ left: iconLeft }}>
        <PickableIcon
          ariaLabel="Change folder icon"
          dimmed={meta.inaccessible}
          pickable={!meta.isShortcut && !meta.inaccessible}
          target={{ id: node.id, kind: "folder" }}
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
