"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import Link from "next/link";
import { ChevronRight, Plus, MoreHorizontal, FileText, Pencil, Trash2, SmilePlus } from "lucide-react";
import { useDraggable, useDroppable } from "@dnd-kit/core";
import type { Page } from "@/types";
import {
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarMenuAction,
} from "@/components/ui/sidebar";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import dynamic from "next/dynamic";
import { useTheme } from "next-themes";
import {
  Popover,
  PopoverContent,
  PopoverAnchor,
} from "@/components/ui/popover";
import data from "@emoji-mart/data";

const EmojiMartPicker = dynamic(() => import("@emoji-mart/react").then((m) => m.default), {
  ssr: false,
  loading: () => <div className="h-[350px] w-[352px]" />,
});

interface PageTreeItemProps {
  page: Page;
  depth: number;
  activePageId: string | null;
  onPageSelect: (pageId: string) => void;
  onAddChild: (parentId: string) => void;
  onDelete: (pageId: string) => void;
  onRename: (pageId: string, newTitle: string) => void;
  onChangeIcon: (pageId: string, icon: string | null) => void;
  isExpanded: boolean;
  onToggleExpand: (id: string) => void;
  isDragOver?: boolean;
  dropIntent?: "before" | "after" | "inside";
  autoRename?: boolean;
  onRenamingDone?: () => void;
}

export function PageTreeItem({
  page,
  depth,
  activePageId,
  onPageSelect,
  onAddChild,
  onDelete,
  onRename,
  onChangeIcon,
  isExpanded,
  onToggleExpand,
  isDragOver,
  dropIntent,
  autoRename,
  onRenamingDone,
}: PageTreeItemProps) {
  const [isRenaming, setIsRenaming] = useState(false);
  const [renameValue, setRenameValue] = useState(page.title);
  const [showEmojiPicker, setShowEmojiPicker] = useState(false);

  const inputRef = useRef<HTMLInputElement>(null);
  const autoRenameHandled = useRef(false);

  // Auto-enter rename mode for newly created pages
  useEffect(() => {
    if (autoRename && !autoRenameHandled.current) {
      autoRenameHandled.current = true;
      setRenameValue(page.title);
      setIsRenaming(true);
    }
    if (!autoRename) {
      autoRenameHandled.current = false;
    }
  }, [autoRename]);

  // Focus and select input when entering rename mode
  useEffect(() => {
    if (isRenaming) {
      requestAnimationFrame(() => {
        inputRef.current?.focus();
        inputRef.current?.select();
      });
    }
  }, [isRenaming]);
  const { resolvedTheme } = useTheme();
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
    [setDragNodeRef, setDropNodeRef]
  );

  // Drop indicator styles
  const dropIndicatorClass = isDragOver
    ? dropIntent === "inside"
      ? "ring-2 ring-sidebar-primary/50 rounded-md bg-sidebar-accent/50"
      : ""
    : "";

  return (
    <SidebarMenuItem
      ref={setNodeRef}
      className={`relative ${isDragging ? "opacity-30" : ""}`}
    >
      {/* Drop indicator line — before */}
      {isDragOver && dropIntent === "before" && (
        <div
          className="absolute top-0 right-2 left-2 h-0.5 rounded-full bg-sidebar-primary"
          style={{ marginLeft: `${depth * 12}px` }}
        />
      )}

      <div
        className={`group/tree-item relative flex items-center ${dropIndicatorClass}`}
        {...attributes}
        {...listeners}
      >
        <button
          className="flex size-6 shrink-0 items-center justify-center rounded-md text-sidebar-foreground/60 hover:text-sidebar-foreground"
          style={{ marginLeft: `${depth * 12}px` }}
          onClick={(e) => {
            e.stopPropagation();
            if (hasChildren) {
              onToggleExpand(page.id);
            }
          }}
        >
          <ChevronRight
            className={`size-3.5 transition-transform duration-200 ${
              hasChildren ? "opacity-100" : "opacity-0"
            } ${isExpanded ? "rotate-90" : ""}`}
          />
        </button>

        {isRenaming ? (
          <div className="flex-1 flex items-center gap-1.5 px-2">
            <span className="shrink-0 text-base leading-none">
              {page.icon || <FileText className="size-4" />}
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
              className="h-7 w-full rounded-md border border-input bg-background px-2 text-sm outline-none focus:ring-1 focus:ring-ring"
            />
          </div>
        ) : (
          <SidebarMenuButton
            asChild
            isActive={isActive}
            className="flex-1 gap-1.5"
            tooltip={page.title}
          >
            <Link
              href={`/docs/${page.id}`}
              onClick={() => onPageSelect(page.id)}
            >
              <span className="shrink-0 text-base leading-none">
                {page.icon || <FileText className="size-4" />}
              </span>
              <span className="truncate">{page.title}</span>
            </Link>
          </SidebarMenuButton>
        )}

        <Popover open={showEmojiPicker} onOpenChange={setShowEmojiPicker}>
          <div className={`absolute right-1 flex items-center gap-0.5 opacity-0 transition-opacity group-hover/tree-item:opacity-100 ${isActive ? "text-sidebar-accent-foreground" : "text-sidebar-foreground/60"}`}>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <PopoverAnchor asChild>
                  <button
                    className="flex size-5 items-center justify-center rounded-md hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
                  >
                    <MoreHorizontal className="size-4" />
                  </button>
                </PopoverAnchor>
              </DropdownMenuTrigger>
              <DropdownMenuContent side="right" align="start" sideOffset={4}>
                <DropdownMenuItem onClick={() => {
                  setRenameValue(page.title);
                  setIsRenaming(true);
                }}>
                  <Pencil className="mr-2 size-4" />
                  Rename
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => setShowEmojiPicker(true)}>
                  <SmilePlus className="mr-2 size-4" />
                  Change icon
                </DropdownMenuItem>
                <DropdownMenuItem
                  className="text-destructive"
                  onClick={() => onDelete(page.id)}
                >
                  <Trash2 className="mr-2 size-4" />
                  Delete
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
            <button
              onClick={(e) => {
                e.stopPropagation();
                onAddChild(page.id);
              }}
              className="flex size-5 items-center justify-center rounded-md hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
            >
              <Plus className="size-3.5" />
            </button>
          </div>
          <PopoverContent
            className="w-auto p-0 border-none shadow-lg"
            side="right"
            align="start"
            sideOffset={8}
            onOpenAutoFocus={(e) => e.preventDefault()}
            onCloseAutoFocus={(e) => e.preventDefault()}
          >
            <EmojiMartPicker
              data={data}
              theme={resolvedTheme === "dark" ? "dark" : "light"}
              onEmojiSelect={(emoji: { native: string }) => {
                onChangeIcon(page.id, emoji.native);
                setShowEmojiPicker(false);
              }}
              previewPosition="none"
              skinTonePosition="search"
              maxFrequentRows={2}
            />
            {page.icon && (
              <div className="border-t px-3 py-2">
                <button
                  type="button"
                  onClick={() => {
                    onChangeIcon(page.id, null);
                    setShowEmojiPicker(false);
                  }}
                  className="text-xs text-muted-foreground hover:text-foreground transition-colors"
                >
                  Remove icon
                </button>
              </div>
            )}
          </PopoverContent>
        </Popover>
      </div>

      {/* Drop indicator line — after */}
      {isDragOver && dropIntent === "after" && (
        <div
          className="absolute bottom-0 right-2 left-2 h-0.5 rounded-full bg-sidebar-primary"
          style={{ marginLeft: `${depth * 12}px` }}
        />
      )}
    </SidebarMenuItem>
  );
}
