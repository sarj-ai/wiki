"use client";

import React, { useState, useEffect, useCallback, useRef } from "react";
import Link from "next/link";
import {
  ChevronRight,
  Plus,
  MoreHorizontal,
  FileText,
  FolderClosed,
  Pencil,
  Trash2,
  SmilePlus,
  Loader2,
  Star,
  StarOff,
  Upload,
  Copy,
  ArrowRightLeft,
} from "lucide-react";
import { useDraggable, useDroppable } from "@dnd-kit/core";
import type { Page } from "@/types";
import { SidebarMenuButton, SidebarMenuItem } from "@/components/ui/sidebar";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { EmojiPickerDialog } from "@/components/emoji-picker";

interface PageTreeItemProps {
  page: Page;
  depth: number;
  activePageId: string | null;
  onAddChild: (parentId: string) => void;
  onDelete: (pageId: string) => void;
  onRename: (pageId: string, newTitle: string) => void;
  onChangeIcon: (pageId: string, icon: string | null) => void;
  isExpanded: boolean;
  onToggleExpand: (id: string) => void;
  isDragOver?: boolean;
  dropPosition?: "before" | "after" | "inside";
  shouldAutoRename?: boolean;
  onRenamingDone?: () => void;
  isAddingChild?: boolean;
  isMoving?: boolean;
  isHighlighted?: boolean;
  isStarred?: boolean;
  onToggleStar?: (pageId: string) => void;
  onImport?: (pageId: string) => void;
  onDuplicate?: (pageId: string) => void;
  onMoveToWorkspace?: (pageId: string) => void;
}

export const PageTreeItem = React.memo(function PageTreeItem({
  page,
  depth,
  activePageId,
  onAddChild,
  onDelete,
  onRename,
  onChangeIcon,
  isExpanded,
  onToggleExpand,
  isDragOver,
  dropPosition,
  shouldAutoRename,
  onRenamingDone,
  isAddingChild,
  isMoving,
  isHighlighted,
  isStarred,
  onToggleStar,
  onImport,
  onDuplicate,
  onMoveToWorkspace,
}: PageTreeItemProps) {
  const [isRenaming, setIsRenaming] = useState(false);
  const [renameValue, setRenameValue] = useState(page.title);
  const [showEmojiPicker, setShowEmojiPicker] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);

  const inputRef = useRef<HTMLInputElement>(null);
  const autoRenameHandled = useRef(false);

  useEffect(() => {
    if (shouldAutoRename && !autoRenameHandled.current) {
      autoRenameHandled.current = true;
      setRenameValue(page.title);
      setIsRenaming(true);
    }
    if (!shouldAutoRename) {
      autoRenameHandled.current = false;
    }
  }, [shouldAutoRename, page.title]);

  // Focus and select input when entering rename mode
  useEffect(() => {
    if (isRenaming) {
      requestAnimationFrame(() => {
        inputRef.current?.focus();
        inputRef.current?.select();
      });
    }
  }, [isRenaming]);
  const hasChildren = page.children.length > 0;
  const isActive = activePageId === page.id;

  const {
    attributes,
    listeners,
    setNodeRef: setDragNodeRef,
    isDragging,
  } = useDraggable({ id: page.id });

  const { setNodeRef: setDropNodeRef } = useDroppable({ id: page.id });

  const setNodeRef = useCallback(
    (node: HTMLElement | null) => {
      setDragNodeRef(node);
      setDropNodeRef(node);
    },
    [setDragNodeRef, setDropNodeRef],
  );

  const dropIndicatorClass =
    isDragOver && dropPosition === "inside"
      ? "ring-2 ring-sidebar-primary/50 rounded-md bg-sidebar-accent/50"
      : "";

  return (
    <SidebarMenuItem
      ref={setNodeRef}
      className={`relative ${isDragging ? "opacity-30" : ""} ${isMoving ? "opacity-50" : ""} ${isHighlighted ? "sidebar-highlight-pulse" : ""}`}
    >
      {/* Drop indicator line — before */}
      {isDragOver && dropPosition === "before" && (
        <div
          className="bg-sidebar-primary absolute top-0 right-2 left-2 h-0.5 rounded-full"
          style={{ marginLeft: `${depth * 12}px` }}
        />
      )}

      <div
        className={`group/tree-item relative flex items-center ${dropIndicatorClass}`}
        {...attributes}
        {...listeners}
      >
        <button
          type="button"
          className="text-sidebar-foreground/60 hover:text-sidebar-foreground flex size-6 shrink-0 items-center justify-center rounded-md"
          style={{ marginLeft: `${depth * 12}px` }}
          onClick={(e) => {
            e.stopPropagation();
            if (hasChildren) {
              onToggleExpand(page.id);
            }
          }}
          aria-label={hasChildren ? (isExpanded ? "Collapse" : "Expand") : undefined}
          aria-expanded={hasChildren ? isExpanded : undefined}
          aria-hidden={!hasChildren}
          tabIndex={hasChildren ? 0 : -1}
        >
          <ChevronRight
            className={`${page.type === "section" ? "size-3" : "size-3.5"} transition-transform duration-200 ${
              hasChildren ? "opacity-100" : "opacity-0"
            } ${isExpanded ? "rotate-90" : ""}`}
          />
        </button>

        {isRenaming ? (
          <div className="flex flex-1 items-center gap-1.5 px-2">
            <span className="shrink-0 text-base leading-none">
              {page.icon ||
                (page.type === "section" ? (
                  <FolderClosed className="size-4" />
                ) : (
                  <FileText className="size-4" />
                ))}
            </span>
            <input
              ref={inputRef}
              value={renameValue}
              onChange={(e) => setRenameValue(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  const trimmed = renameValue.trim();
                  if (trimmed && trimmed !== page.title) {
                    onRename(page.id, trimmed);
                  }
                  setIsRenaming(false);
                  onRenamingDone?.();
                }
                if (e.key === "Escape") {
                  setRenameValue(page.title);
                  setIsRenaming(false);
                  onRenamingDone?.();
                }
              }}
              onBlur={() => {
                const trimmed = renameValue.trim();
                if (trimmed && trimmed !== page.title) {
                  onRename(page.id, trimmed);
                }
                setIsRenaming(false);
                onRenamingDone?.();
              }}
              className="border-input bg-background focus:ring-ring h-7 w-full rounded-md border px-2 text-sm outline-none focus:ring-1"
            />
          </div>
        ) : (
          <SidebarMenuButton
            asChild
            isActive={isActive}
            className="flex-1 gap-1.5"
            tooltip={page.title}
          >
            <Link href={`/docs/${page.id}`}>
              <span className="shrink-0 text-base leading-none">
                {isMoving ? (
                  <Loader2 className="size-4 animate-spin" />
                ) : (
                  page.icon ||
                  (page.type === "section" ? (
                    <FolderClosed className="size-4" />
                  ) : (
                    <FileText className="size-4" />
                  ))
                )}
              </span>
              <span className="truncate" title={page.title}>
                {page.title}
              </span>
              {isStarred && <Star className="size-2.5 shrink-0 fill-yellow-400 text-yellow-400" />}
            </Link>
          </SidebarMenuButton>
        )}

        <div
          className={`bg-sidebar absolute right-0.5 flex items-center gap-0.5 rounded-md px-0.5 opacity-0 transition-opacity group-hover/tree-item:opacity-100 ${isActive ? "bg-sidebar-accent text-sidebar-accent-foreground" : "text-sidebar-foreground/60"}`}
        >
          <DropdownMenu open={menuOpen} onOpenChange={setMenuOpen}>
            <DropdownMenuTrigger asChild>
              <button
                type="button"
                className="hover:bg-sidebar-accent hover:text-sidebar-accent-foreground flex size-5 items-center justify-center rounded-md"
                aria-label="Page options"
              >
                <MoreHorizontal className="size-4" />
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent
              side="right"
              align="start"
              sideOffset={4}
              onCloseAutoFocus={(e) => {
                // Prevent focus restoration when rename is about to activate
                if (isRenaming) e.preventDefault();
              }}
            >
              <DropdownMenuItem
                onSelect={(e) => {
                  e.preventDefault();
                  setMenuOpen(false);
                  setTimeout(() => {
                    setRenameValue(page.title);
                    setIsRenaming(true);
                  }, 100);
                }}
              >
                <Pencil className="mr-2 size-4" />
                Rename
              </DropdownMenuItem>
              <DropdownMenuItem
                onSelect={(e) => {
                  e.preventDefault();
                  setMenuOpen(false);
                  setShowEmojiPicker(true);
                }}
              >
                <SmilePlus className="mr-2 size-4" />
                Change icon
              </DropdownMenuItem>
              {onToggleStar && (
                <DropdownMenuItem onClick={() => onToggleStar(page.id)}>
                  {isStarred ? (
                    <>
                      <StarOff className="mr-2 size-4" />
                      Unstar
                    </>
                  ) : (
                    <>
                      <Star className="mr-2 size-4" />
                      Star
                    </>
                  )}
                </DropdownMenuItem>
              )}
              {onImport && (
                <DropdownMenuItem onClick={() => onImport(page.id)}>
                  <Upload className="mr-2 size-4" />
                  Import pages
                </DropdownMenuItem>
              )}
              {onDuplicate && page.type === "page" && (
                <DropdownMenuItem onClick={() => onDuplicate(page.id)}>
                  <Copy className="mr-2 size-4" />
                  Duplicate
                </DropdownMenuItem>
              )}
              {onMoveToWorkspace && !page.isPrivate && (
                <DropdownMenuItem onClick={() => onMoveToWorkspace(page.id)}>
                  <ArrowRightLeft className="mr-2 size-4" />
                  Move to workspace
                </DropdownMenuItem>
              )}
              <DropdownMenuItem className="text-destructive" onClick={() => onDelete(page.id)}>
                <Trash2 className="mr-2 size-4" />
                Delete
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              if (!isAddingChild) onAddChild(page.id);
            }}
            disabled={isAddingChild}
            className="hover:bg-sidebar-accent hover:text-sidebar-accent-foreground flex size-5 items-center justify-center rounded-md"
            aria-label="Add child page"
          >
            {isAddingChild ? (
              <Loader2 className="size-3.5 animate-spin" />
            ) : (
              <Plus className="size-3.5" />
            )}
          </button>
        </div>
        <EmojiPickerDialog
          currentEmoji={page.icon}
          onEmojiSelect={(emoji) => {
            onChangeIcon(page.id, emoji);
            setShowEmojiPicker(false);
          }}
          onRemove={
            page.icon
              ? () => {
                  onChangeIcon(page.id, null);
                  setShowEmojiPicker(false);
                }
              : undefined
          }
          open={showEmojiPicker}
          onOpenChange={setShowEmojiPicker}
        />
      </div>

      {/* Drop indicator line — after */}
      {isDragOver && dropPosition === "after" && (
        <div
          className="bg-sidebar-primary absolute right-2 bottom-0 left-2 h-0.5 rounded-full"
          style={{ marginLeft: `${depth * 12}px` }}
        />
      )}
    </SidebarMenuItem>
  );
});
