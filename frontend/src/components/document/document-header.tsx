"use client";

import { Fragment, useState, useRef, useEffect } from "react";
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
import { ShareButton } from "@/components/permissions/share-button";
import { EmojiPicker } from "@/components/emoji-picker";
import { SmilePlus, PanelLeftClose, PanelLeft, Maximize2, Search, Star } from "lucide-react";
import { useSidebar } from "@/components/ui/sidebar";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

function formatRelativeTime(dateStr: string): string {
  const now = Date.now();
  const then = new Date(dateStr).getTime();
  const diffMs = now - then;
  const diffMin = Math.floor(diffMs / 60000);
  if (diffMin < 1) return "just now";
  if (diffMin === 1) return "1 min ago";
  if (diffMin < 60) return `${diffMin} mins ago`;
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr === 1) return "1 hour ago";
  if (diffHr < 24) return `${diffHr} hours ago`;
  const diffDay = Math.floor(diffHr / 24);
  if (diffDay === 1) return "yesterday";
  if (diffDay < 30) return `${diffDay} days ago`;
  const diffMo = Math.floor(diffDay / 30);
  if (diffMo === 1) return "1 month ago";
  if (diffMo < 12) return `${diffMo} months ago`;
  return new Date(dateStr).toLocaleDateString();
}

const TOOLBAR_BUTTON_CLASS =
  "flex size-7 items-center justify-center rounded-md text-muted-foreground hover:text-white hover:bg-[#251040] dark:hover:bg-[#4B3483] transition-colors";

interface DocumentToolbarProps {
  page: Page;
  breadcrumbs: Page[];
  workspace: Workspace | null;
  onFocusMode?: () => void;
  isStarred?: boolean;
  onToggleStar?: () => void;
  children?: React.ReactNode;
}

export function DocumentToolbar({
  page,
  breadcrumbs,
  workspace,
  onFocusMode,
  isStarred,
  onToggleStar,
  children,
}: DocumentToolbarProps) {
  const { open: sidebarOpen, toggleSidebar } = useSidebar();

  return (
    <div className="bg-background flex shrink-0 items-center gap-2 px-6 py-2">
      <Tooltip>
        <TooltipTrigger asChild>
          <button type="button" onClick={toggleSidebar} className={TOOLBAR_BUTTON_CLASS}>
            {sidebarOpen ? <PanelLeftClose className="size-4" /> : <PanelLeft className="size-4" />}
          </button>
        </TooltipTrigger>
        <TooltipContent side="bottom">
          {sidebarOpen ? "Hide sidebar" : "Show sidebar"}
        </TooltipContent>
      </Tooltip>

      {(breadcrumbs.length > 1 || workspace || page.isPrivate) && (
        <Breadcrumb>
          <BreadcrumbList>
            {workspace && (
              <>
                <BreadcrumbItem>
                  <BreadcrumbLink href="/">{workspace.name}</BreadcrumbLink>
                </BreadcrumbItem>
                <BreadcrumbSeparator />
              </>
            )}
            {page.isPrivate && !workspace && (
              <>
                <BreadcrumbItem>
                  <BreadcrumbLink href="/">My Drafts</BreadcrumbLink>
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
                      <BreadcrumbLink href={`/docs/${crumb.id}`}>{crumb.title}</BreadcrumbLink>
                    )}
                  </BreadcrumbItem>
                  {!isLast && <BreadcrumbSeparator />}
                </Fragment>
              );
            })}
          </BreadcrumbList>
        </Breadcrumb>
      )}

      <div className="ml-auto flex items-center gap-2">
        <Tooltip>
          <TooltipTrigger asChild>
            <button
              type="button"
              onClick={() => {
                // Trigger CommandPalette's Cmd+K listener — coupled to command-palette.tsx
                window.dispatchEvent(new KeyboardEvent("keydown", { key: "k", metaKey: true }));
              }}
              className={TOOLBAR_BUTTON_CLASS}
            >
              <Search className="size-4" />
            </button>
          </TooltipTrigger>
          <TooltipContent side="bottom">Search (⌘K)</TooltipContent>
        </Tooltip>
        {children}
        {onFocusMode && (
          <Tooltip>
            <TooltipTrigger asChild>
              <button type="button" onClick={onFocusMode} className={TOOLBAR_BUTTON_CLASS}>
                <Maximize2 className="size-4" />
              </button>
            </TooltipTrigger>
            <TooltipContent side="bottom">Focus mode (⌘.)</TooltipContent>
          </Tooltip>
        )}
        {onToggleStar && (
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                type="button"
                onClick={onToggleStar}
                className="hover:bg-accent flex size-8 items-center justify-center rounded-md transition-colors"
              >
                <Star
                  className={`size-3.5 ${isStarred ? "fill-yellow-400 text-yellow-400" : "text-muted-foreground"}`}
                />
              </button>
            </TooltipTrigger>
            <TooltipContent side="bottom">{isStarred ? "Unstar" : "Star"}</TooltipContent>
          </Tooltip>
        )}
        <ShareButton page={page} workspace={workspace} />
      </div>
    </div>
  );
}

interface DocumentTitleProps {
  page: Page;
  workspace: Workspace | null;
  onChangeIcon?: (icon: string | null) => void;
  onRename?: (newTitle: string) => void;
}

export function DocumentTitle({ page, workspace, onChangeIcon, onRename }: DocumentTitleProps) {
  const [isEditing, setIsEditing] = useState(false);
  const [editValue, setEditValue] = useState(page.title);
  const inputRef = useRef<HTMLInputElement>(null);

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
    <div className="flex flex-col gap-3 px-6 pt-4 pb-4">
      {onChangeIcon && !page.icon && (
        <EmojiPicker currentEmoji={page.icon} onEmojiSelect={(emoji) => onChangeIcon(emoji)}>
          <button
            type="button"
            className="text-muted-foreground hover:text-foreground hover:bg-accent -ml-2 flex w-fit items-center gap-1.5 rounded-md px-2 py-1 text-sm transition-colors"
          >
            <SmilePlus className="size-4" />
            <span>Add icon</span>
          </button>
        </EmojiPicker>
      )}

      <div className="flex items-start gap-3">
        {onChangeIcon && page.icon && (
          <EmojiPicker
            currentEmoji={page.icon}
            onEmojiSelect={(emoji) => onChangeIcon(emoji)}
            onRemove={() => onChangeIcon(null)}
          >
            <button
              type="button"
              className="hover:bg-accent -m-1 shrink-0 rounded-md p-1 text-[40px] leading-none transition-colors"
              title="Change icon"
            >
              {page.icon}
            </button>
          </EmojiPicker>
        )}

        {!onChangeIcon && page.icon && (
          <span className="shrink-0 text-[40px] leading-none">{page.icon}</span>
        )}

        <div className="flex min-w-0 flex-1 flex-col gap-1.5">
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
                className="text-foreground ring-ring -ml-2 w-full rounded-md border-none bg-transparent px-2 text-3xl font-bold tracking-tight ring-1 outline-none"
              />
            ) : (
              <h1
                className={`text-foreground truncate text-3xl font-bold tracking-tight ${
                  onRename
                    ? "hover:bg-accent/50 -ml-2 cursor-text rounded-md px-2 transition-colors"
                    : ""
                }`}
                onClick={() => {
                  if (onRename) {
                    setIsEditing(true);
                  }
                }}
                onKeyDown={(e) => {
                  if (onRename && (e.key === "Enter" || e.key === " ")) {
                    e.preventDefault();
                    setIsEditing(true);
                  }
                }}
                role={onRename ? "button" : undefined}
                tabIndex={onRename ? 0 : undefined}
                aria-label={onRename ? "Click to rename" : undefined}
                title={onRename ? "Click to rename" : undefined}
              >
                {page.title}
              </h1>
            )}
          </div>
          <div className="flex items-center gap-2">
            {page.isPrivate ? (
              <Badge variant="secondary" className="text-xs">
                My Drafts
              </Badge>
            ) : workspace ? (
              <Badge variant="outline" className="text-xs">
                {workspace.name}
              </Badge>
            ) : null}
            {page.updatedAt && (
              <span className="text-muted-foreground text-xs">
                Edited {formatRelativeTime(page.updatedAt)}
                {(page.lastEditedByName || page.lastEditedByEmail) &&
                  ` by ${page.lastEditedByName ?? page.lastEditedByEmail!.split("@")[0]}`}
              </span>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
