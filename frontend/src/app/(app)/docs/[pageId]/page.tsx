"use client";

import { use, useEffect, useRef, useState } from "react";
import {
  ExternalLink,
  FileQuestion,
  FolderOpen,
  Minimize2,
  PanelLeft,
  PanelLeftClose,
  Pencil,
  Eye,
  Settings2,
  Search,
} from "lucide-react";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useDocumentData } from "@/hooks/use-document-data";
import { useEditorPreference } from "@/hooks/use-editor-preference";
import { useFocusMode } from "@/hooks/use-focus-mode";
import { useStarredPages } from "@/hooks/use-starred-pages";
import { DocumentToolbar, DocumentTitle } from "@/components/document/document-header";
import { DocumentSkeleton } from "@/components/document/document-skeleton";
import { DocumentContent } from "@/components/document/document-content";

const TOOLBAR_BUTTON_CLASS = "flex size-7 items-center justify-center rounded-md transition-colors";
const TOOLBAR_BUTTON_IDLE =
  "text-muted-foreground hover:text-white hover:bg-[#251040] dark:hover:bg-[#4B3483]";
const TOOLBAR_BUTTON_ACTIVE = "bg-sidebar-accent text-sidebar-accent-foreground";

function EditToggleButton({
  isEditing,
  onToggle,
  highlight,
}: {
  isEditing: boolean;
  onToggle: () => void;
  highlight?: boolean;
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          data-edit-toggle
          onClick={onToggle}
          className={`${TOOLBAR_BUTTON_CLASS} ${isEditing ? TOOLBAR_BUTTON_ACTIVE : TOOLBAR_BUTTON_IDLE} ${highlight ? "edit-toggle-highlight" : ""}`}
        >
          {isEditing ? <Eye className="size-4" /> : <Pencil className="size-4" />}
        </button>
      </TooltipTrigger>
      <TooltipContent side="bottom">{isEditing ? "View mode" : "Edit mode"}</TooltipContent>
    </Tooltip>
  );
}

export default function DocumentPage({ params }: { params: Promise<{ pageId: string }> }) {
  const { pageId } = use(params);
  const [isEditing, setIsEditing] = useState(false);

  // Reset editing state when navigating between pages
  useEffect(() => {
    setIsEditing(false);
  }, [pageId]);

  const { alwaysIframe, toggleAlwaysIframe } = useEditorPreference();
  const { starredIds, toggleStar } = useStarredPages();
  const { focusMode, sidebarOpen, toggleSidebar, enterFocusMode, exitFocusMode } = useFocusMode();

  // ── Data hook ──
  const {
    page,
    isLoading,
    error,
    content,
    isContentReady,
    refetchContent,
    isPending,
    docEmpty,
    workspace,
    breadcrumbs,
    pendingElapsed,
    retrying,
    handleRetry,
    handleDeleteStale,
    handleChangeIcon,
    handleRename,
    forceRefreshContent,
  } = useDocumentData(pageId);

  // ── Edit→view transition: force-refresh content ──
  const wasEditing = useRef(false);
  useEffect(() => {
    if (wasEditing.current && !isEditing) {
      forceRefreshContent();
    }
    wasEditing.current = isEditing;
  }, [isEditing, alwaysIframe, pageId, forceRefreshContent]);

  // ── Derived state ──
  const showIframe = !isPending && (alwaysIframe || isEditing);

  // ── Loading state ──
  if (isLoading) {
    return <DocumentSkeleton />;
  }

  // ── Section page (folder, no document content) ──
  if (page?.type === "section") {
    const handleShowChildren = () => {
      // Dispatch a custom event that the sidebar can listen for to expand + highlight
      window.dispatchEvent(new CustomEvent("sarj:expand-section", { detail: { pageId: page.id } }));
    };

    return (
      <div className="flex h-svh flex-col">
        <DocumentToolbar
          page={page}
          breadcrumbs={breadcrumbs}
          workspace={workspace}
          isStarred={starredIds.has(page.id)}
          onToggleStar={() => toggleStar(page.id)}
        >
          <div />
        </DocumentToolbar>
        <div className="flex-1 overflow-y-auto">
          <DocumentTitle
            page={page}
            workspace={workspace}
            onChangeIcon={handleChangeIcon}
            onRename={handleRename}
          />
          <div className="animate-in fade-in flex min-h-[300px] flex-1 items-center justify-center duration-300">
            <div className="flex flex-col items-center gap-5">
              <div className="bg-muted flex size-16 items-center justify-center rounded-2xl">
                <FolderOpen className="text-muted-foreground size-8" />
              </div>
              <div className="text-center">
                <h3 className="text-foreground text-base font-medium">This is a folder</h3>
                <p className="text-muted-foreground mt-1 max-w-xs text-sm">
                  It contains documents and subfolders. Browse its contents in the sidebar.
                </p>
              </div>
              <button
                type="button"
                onClick={handleShowChildren}
                className="bg-primary text-primary-foreground inline-flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-medium shadow-sm transition-all hover:opacity-90 active:scale-[0.98]"
              >
                <FolderOpen className="size-4" />
                Show contents
              </button>
            </div>
          </div>
        </div>
      </div>
    );
  }

  // ── Error / not found ──
  if (error || !page) {
    return (
      <div className="flex h-full min-h-svh flex-col items-center justify-center gap-4">
        <div className="bg-muted flex size-16 items-center justify-center rounded-2xl">
          <FileQuestion className="text-muted-foreground size-8" />
        </div>
        <div className="text-center">
          <h2 className="text-foreground text-lg font-medium">Page not found</h2>
          <p className="text-muted-foreground mt-1 text-sm">
            The document you are looking for does not exist or you do not have access.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-svh flex-col">
      {focusMode ? (
        <div className="bg-background flex shrink-0 items-center gap-2 border-b px-3 py-1.5">
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                type="button"
                onClick={toggleSidebar}
                className={`${TOOLBAR_BUTTON_CLASS} ${TOOLBAR_BUTTON_IDLE}`}
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
          <span className="flex-1 truncate text-sm font-medium">
            {page.icon && <span className="mr-1.5">{page.icon}</span>}
            {page.title}
          </span>
          <div className="flex items-center gap-1">
            <Tooltip>
              <TooltipTrigger asChild>
                <button
                  type="button"
                  onClick={() =>
                    window.dispatchEvent(new KeyboardEvent("keydown", { key: "k", metaKey: true }))
                  }
                  className={`${TOOLBAR_BUTTON_CLASS} ${TOOLBAR_BUTTON_IDLE}`}
                >
                  <Search className="size-4" />
                </button>
              </TooltipTrigger>
              <TooltipContent side="bottom">Search (⌘K)</TooltipContent>
            </Tooltip>
            {!isPending && (
              <Tooltip>
                <TooltipTrigger asChild>
                  <a
                    href={`https://docs.google.com/document/d/${page.googleDriveId}/edit`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className={`${TOOLBAR_BUTTON_CLASS} ${TOOLBAR_BUTTON_IDLE}`}
                  >
                    <ExternalLink className="size-4" />
                  </a>
                </TooltipTrigger>
                <TooltipContent side="bottom">Open in Google Docs</TooltipContent>
              </Tooltip>
            )}
            {!alwaysIframe && !isPending && page?.currentUserPermission !== "view" && (
              <EditToggleButton
                isEditing={isEditing}
                onToggle={() => setIsEditing(!isEditing)}
                highlight={docEmpty}
              />
            )}
            <Tooltip>
              <TooltipTrigger asChild>
                <button
                  type="button"
                  onClick={exitFocusMode}
                  className={`${TOOLBAR_BUTTON_CLASS} ${TOOLBAR_BUTTON_IDLE}`}
                >
                  <Minimize2 className="size-4" />
                </button>
              </TooltipTrigger>
              <TooltipContent side="bottom">Exit focus mode (⌘.)</TooltipContent>
            </Tooltip>
          </div>
        </div>
      ) : (
        <DocumentToolbar
          page={page}
          breadcrumbs={breadcrumbs}
          workspace={workspace}
          onFocusMode={enterFocusMode}
          isStarred={starredIds.has(page.id)}
          onToggleStar={() => toggleStar(page.id)}
        >
          <div className="flex items-center gap-1">
            {!isPending && (
              <Tooltip>
                <TooltipTrigger asChild>
                  <a
                    href={`https://docs.google.com/document/d/${page.googleDriveId}/edit`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className={`${TOOLBAR_BUTTON_CLASS} ${TOOLBAR_BUTTON_IDLE}`}
                  >
                    <ExternalLink className="size-4" />
                  </a>
                </TooltipTrigger>
                <TooltipContent side="bottom">Open in Google Docs</TooltipContent>
              </Tooltip>
            )}
            {!alwaysIframe && !isPending && page?.currentUserPermission !== "view" && (
              <EditToggleButton
                isEditing={isEditing}
                onToggle={() => setIsEditing(!isEditing)}
                highlight={docEmpty}
              />
            )}
            <Tooltip>
              <TooltipTrigger asChild>
                <button
                  type="button"
                  onClick={toggleAlwaysIframe}
                  className={`${TOOLBAR_BUTTON_CLASS} ${alwaysIframe ? TOOLBAR_BUTTON_ACTIVE : TOOLBAR_BUTTON_IDLE}`}
                >
                  <Settings2 className="size-4" />
                </button>
              </TooltipTrigger>
              <TooltipContent side="bottom">
                {alwaysIframe
                  ? "Using Google Docs editor (click to use native viewer)"
                  : "Using native viewer (click to always use Google Docs editor)"}
              </TooltipContent>
            </Tooltip>
          </div>
        </DocumentToolbar>
      )}

      <div className={`flex-1 overflow-y-auto ${showIframe ? "flex flex-col" : ""}`}>
        {!focusMode && (
          <DocumentTitle
            page={page}
            workspace={workspace}
            onChangeIcon={handleChangeIcon}
            onRename={handleRename}
          />
        )}
        <DocumentContent
          isPending={isPending}
          showIframe={showIframe}
          isContentReady={isContentReady}
          isEditing={isEditing}
          focusMode={focusMode}
          googleDriveId={page.googleDriveId}
          content={content}
          pendingElapsed={pendingElapsed}
          retrying={retrying}
          onRetry={handleRetry}
          onDeleteStale={handleDeleteStale}
          onStartEditing={() => setIsEditing(true)}
          onRefetchContent={() => refetchContent()}
        />
      </div>
    </div>
  );
}
