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
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useDocumentData } from "@/hooks/use-document-data";
import { useEditorPreference } from "@/hooks/use-editor-preference";
import { useFocusMode } from "@/hooks/use-focus-mode";
import { DocumentToolbar, DocumentTitle } from "@/components/document/document-header";
import { DocumentSkeleton } from "@/components/document/document-skeleton";
import { DocumentContent } from "@/components/document/document-content";

const TOOLBAR_BUTTON_CLASS =
  "flex size-7 items-center justify-center rounded-md transition-colors";
const TOOLBAR_BUTTON_IDLE =
  "text-muted-foreground hover:text-white hover:bg-[#251040] dark:hover:bg-[#4B3483]";
const TOOLBAR_BUTTON_ACTIVE =
  "bg-sidebar-accent text-sidebar-accent-foreground";

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
      <TooltipContent side="bottom">
        {isEditing ? "View mode" : "Edit mode"}
      </TooltipContent>
    </Tooltip>
  );
}

export default function DocumentPage({
  params,
}: {
  params: Promise<{ pageId: string }>;
}) {
  const { pageId } = use(params);
  const [isEditing, setIsEditing] = useState(false);

  // Reset editing state when navigating between pages
  useEffect(() => {
    setIsEditing(false);
  }, [pageId]);

  const { alwaysIframe, toggleAlwaysIframe } = useEditorPreference();
  const {
    focusMode,
    sidebarOpen,
    toggleSidebar,
    enterFocusMode,
    exitFocusMode,
  } = useFocusMode();

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
      window.dispatchEvent(
        new CustomEvent("sarj:expand-section", { detail: { pageId: page.id } })
      );
    };

    return (
      <div className="flex flex-col h-svh">
        <DocumentToolbar
          page={page}
          breadcrumbs={breadcrumbs}
          workspace={workspace}
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
          <div className="flex-1 flex items-center justify-center animate-in fade-in duration-300 min-h-[300px]">
            <div className="flex flex-col items-center gap-5">
              <div className="flex size-16 items-center justify-center rounded-2xl bg-muted">
                <FolderOpen className="size-8 text-muted-foreground" />
              </div>
              <div className="text-center">
                <h3 className="text-base font-medium text-foreground">
                  This is a folder
                </h3>
                <p className="mt-1 text-sm text-muted-foreground max-w-xs">
                  It contains documents and subfolders. Browse its contents in the sidebar.
                </p>
              </div>
              <button
                type="button"
                onClick={handleShowChildren}
                className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow-sm transition-all hover:opacity-90 active:scale-[0.98]"
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
        <div className="flex size-16 items-center justify-center rounded-2xl bg-muted">
          <FileQuestion className="size-8 text-muted-foreground" />
        </div>
        <div className="text-center">
          <h2 className="text-lg font-medium text-foreground">
            Page not found
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">
            The document you are looking for does not exist or you do not have
            access.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-svh">
      {focusMode ? (
        <div className="shrink-0 flex items-center gap-2 px-3 py-1.5 border-b bg-background">
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
          <span className="text-sm font-medium truncate flex-1">
            {page.icon && <span className="mr-1.5">{page.icon}</span>}
            {page.title}
          </span>
          <div className="flex items-center gap-1">
            <Tooltip>
              <TooltipTrigger asChild>
                <button
                  type="button"
                  onClick={() => window.dispatchEvent(new KeyboardEvent("keydown", { key: "k", metaKey: true }))}
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
                <TooltipContent side="bottom">
                  Open in Google Docs
                </TooltipContent>
              </Tooltip>
            )}
            {!alwaysIframe && !isPending && (
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
              <TooltipContent side="bottom">
                Exit focus mode (⌘.)
              </TooltipContent>
            </Tooltip>
          </div>
        </div>
      ) : (
        <DocumentToolbar
          page={page}
          breadcrumbs={breadcrumbs}
          workspace={workspace}
          onFocusMode={enterFocusMode}
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
                <TooltipContent side="bottom">
                  Open in Google Docs
                </TooltipContent>
              </Tooltip>
            )}
            {!alwaysIframe && !isPending && (
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

      <div
        className={`flex-1 overflow-y-auto ${showIframe ? "flex flex-col" : ""}`}
      >
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
