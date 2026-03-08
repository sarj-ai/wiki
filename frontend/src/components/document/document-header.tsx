"use client";

import { useState, useRef, useEffect } from "react";
import type { Page, Workspace } from "@/types";
import { Badge } from "@/components/ui/badge";
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb";
import { Fragment } from "react";
import { ShareButton } from "@/components/permissions/share-button";
import { EmojiPicker } from "@/components/emoji-picker";
import { SmilePlus, PanelLeftClose, PanelLeft, Maximize2 } from "lucide-react";
import { useSidebar } from "@/components/ui/sidebar";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";

interface DocumentHeaderProps {
  page: Page;
  breadcrumbs: Page[];
  workspace: Workspace | null;
  onChangeIcon?: (icon: string | null) => void;
  onRename?: (newTitle: string) => void;
  onFocusMode?: () => void;
}

export function DocumentHeader({
  page,
  breadcrumbs,
  workspace,
  onChangeIcon,
  onRename,
  onFocusMode,
}: DocumentHeaderProps) {
  const { open: sidebarOpen, toggleSidebar } = useSidebar();
  const [isEditing, setIsEditing] = useState(false);
  const [editValue, setEditValue] = useState(page.title);
  const inputRef = useRef<HTMLInputElement>(null);

  // Sync edit value when page title changes externally
  useEffect(() => {
    if (!isEditing) setEditValue(page.title);
  }, [page.title, isEditing]);

  useEffect(() => {
    if (isEditing && inputRef.current) {
      inputRef.current.focus();
      inputRef.current.select();
    }
  }, [isEditing]);

  function commitRename() {
    const trimmed = editValue.trim();
    if (trimmed && trimmed !== page.title) {
      onRename?.(trimmed);
    } else {
      setEditValue(page.title);
    }
    setIsEditing(false);
  }

  return (
    <div className="flex flex-col gap-3">
      {/* Top bar: sidebar toggle + breadcrumb */}
      <div className="flex items-center gap-2">
        <Tooltip>
          <TooltipTrigger asChild>
            <button
              type="button"
              onClick={toggleSidebar}
              className="flex size-7 items-center justify-center rounded-md text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
            >
              {sidebarOpen ? (
                <PanelLeftClose className="size-4" />
              ) : (
                <PanelLeft className="size-4" />
              )}
            </button>
          </TooltipTrigger>
          <TooltipContent side="bottom">
            {sidebarOpen ? "Hide sidebar" : "Show sidebar"}
          </TooltipContent>
        </Tooltip>

        {breadcrumbs.length > 1 && (
          <Breadcrumb>
          <BreadcrumbList>
            {workspace && (
              <>
                <BreadcrumbItem>
                  <BreadcrumbLink href="/">
                    {workspace.name}
                  </BreadcrumbLink>
                </BreadcrumbItem>
                <BreadcrumbSeparator />
              </>
            )}
            {breadcrumbs.map((crumb, index) => {
              const isLast = index === breadcrumbs.length - 1;
              return (
                <Fragment key={crumb.id}>
                  <BreadcrumbItem>
                    {isLast ? (
                      <BreadcrumbPage>{crumb.title}</BreadcrumbPage>
                    ) : (
                      <BreadcrumbLink href={`/docs/${crumb.id}`}>
                        {crumb.title}
                      </BreadcrumbLink>
                    )}
                  </BreadcrumbItem>
                  {!isLast && <BreadcrumbSeparator />}
                </Fragment>
              );
            })}
          </BreadcrumbList>
        </Breadcrumb>
        )}

        {onFocusMode && (
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                type="button"
                onClick={onFocusMode}
                className="ml-auto flex size-7 items-center justify-center rounded-md text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
              >
                <Maximize2 className="size-4" />
              </button>
            </TooltipTrigger>
            <TooltipContent side="bottom">Focus mode (⌘.)</TooltipContent>
          </Tooltip>
        )}
      </div>

      {/* Add emoji button — shown when no icon is set */}
      {onChangeIcon && !page.icon && (
        <EmojiPicker
          currentEmoji={page.icon}
          onEmojiSelect={(emoji) => onChangeIcon(emoji)}
        >
          <button
            type="button"
            className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground hover:bg-accent rounded-md px-2 py-1 -ml-2 transition-colors w-fit"
          >
            <SmilePlus className="size-4" />
            <span>Add icon</span>
          </button>
        </EmojiPicker>
      )}

      {/* Title row */}
      <div className="flex items-start gap-3">
        {onChangeIcon && page.icon && (
          <EmojiPicker
            currentEmoji={page.icon}
            onEmojiSelect={(emoji) => onChangeIcon(emoji)}
            onRemove={() => onChangeIcon(null)}
          >
            <button
              type="button"
              className="shrink-0 text-[40px] leading-none hover:bg-accent rounded-md p-1 -m-1 transition-colors"
              title="Change icon"
            >
              {page.icon}
            </button>
          </EmojiPicker>
        )}

        {!onChangeIcon && page.icon && (
          <span className="text-[40px] leading-none shrink-0">
            {page.icon}
          </span>
        )}

        <div className="flex flex-col gap-1.5 min-w-0 flex-1">
          <div className="flex items-center justify-between gap-3">
            {isEditing ? (
              <input
                ref={inputRef}
                value={editValue}
                onChange={(e) => setEditValue(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") commitRename();
                  if (e.key === "Escape") {
                    setEditValue(page.title);
                    setIsEditing(false);
                  }
                }}
                onBlur={commitRename}
                className="text-3xl font-bold tracking-tight text-foreground bg-transparent border-none outline-none ring-1 ring-ring rounded-md px-2 -ml-2 w-full"
              />
            ) : (
              <h1
                className={`text-3xl font-bold tracking-tight text-foreground truncate ${
                  onRename ? "cursor-text hover:bg-accent/50 rounded-md px-2 -ml-2 transition-colors" : ""
                }`}
                onClick={() => {
                  if (onRename) {
                    setIsEditing(true);
                  }
                }}
                title={onRename ? "Click to rename" : undefined}
              >
                {page.title}
              </h1>
            )}
            <ShareButton page={page} workspace={workspace} />
          </div>
          <div className="flex items-center gap-2">
            {page.isPrivate ? (
              <Badge variant="secondary" className="text-xs">
                Private
              </Badge>
            ) : workspace ? (
              <Badge variant="outline" className="text-xs">
                {workspace.name}
              </Badge>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  );
}
