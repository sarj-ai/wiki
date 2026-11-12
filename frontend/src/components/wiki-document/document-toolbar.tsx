"use client";

import {
  Check,
  ChevronRight,
  Copy,
  ExternalLink,
  Eye,
  Pencil,
  RefreshCw,
} from "lucide-react";
import { Fragment, type ReactNode, type Ref, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  iframeShowsOwnTitle,
  openInGoogleLabel,
  renderMimeIcon,
} from "@/lib/file-icons";
import { type DocumentMode, supportsDocumentMode } from "@/lib/file-urls";
import type { BreadcrumbItem } from "@/types/wiki";

import { useCopyFeedback } from "./use-copy-feedback";

interface DocumentToolbarProps {
  breadcrumb: BreadcrumbItem[];
  children?: ReactNode;
  copyHref?: string;
  documentMode: DocumentMode;
  editorViewEnabled: boolean;
  fileId: string;
  mimeType: string;
  onDocumentModeChange: (mode: DocumentMode) => void;
  onReload: () => void;
  openUrl: null | string;
  reloadButtonRef?: Ref<HTMLButtonElement> | undefined;
  showDocumentTitle?: boolean;
  showModeSwitch?: boolean;
  title: string;
}

export function DocumentToolbar(props: DocumentToolbarProps) {
  return <DocumentToolbarContent key={props.fileId} {...props} />;
}

function DocumentToolbarContent({
  breadcrumb,
  children,
  copyHref,
  documentMode,
  editorViewEnabled,
  fileId,
  mimeType,
  onDocumentModeChange,
  onReload,
  openUrl,
  reloadButtonRef,
  showDocumentTitle = !iframeShowsOwnTitle(mimeType),
  showModeSwitch = true,
  title,
}: DocumentToolbarProps) {
  const { copied, showCopied } = useCopyFeedback(fileId);
  const [copyFailed, setCopyFailed] = useState(false);
  const trail = showDocumentTitle
    ? [...breadcrumb, { id: fileId, name: title }]
    : breadcrumb;
  const openLabel = openInGoogleLabel(mimeType);
  const handleCopy = () => {
    setCopyFailed(false);
    (async () => {
      await navigator.clipboard.writeText(
        copyHref === undefined
          ? window.location.href
          : new URL(copyHref, window.location.origin).href,
      );
      showCopied();
    })().catch(() => setCopyFailed(true));
  };

  return (
    <>
      <header className="border-border bg-background text-foreground @container/toolbar flex min-h-10 shrink-0 flex-wrap items-center gap-1 overflow-hidden border-b px-4 py-1 sm:gap-2">
        <div className="flex min-w-20 basis-full items-center gap-2 overflow-hidden @min-[40rem]/toolbar:flex-1">
          <span aria-hidden className="shrink-0">
            {renderMimeIcon(mimeType, "size-4")}
          </span>
          <nav
            aria-label="Breadcrumb"
            className="flex min-w-0 items-center gap-1 text-sm"
            title={trail.map((crumb) => crumb.name).join(" / ")}
          >
            {trail.map((crumb, index) => (
              <Fragment key={crumb.id}>
                {index > 0 ? (
                  <ChevronRight
                    aria-hidden
                    className="text-muted-foreground hidden size-3.5 shrink-0 @min-[40rem]/toolbar:block"
                  />
                ) : null}
                {showDocumentTitle && index === trail.length - 1 ? (
                  <h1 className="truncate font-medium">{crumb.name}</h1>
                ) : (
                  <span
                    className={
                      index === trail.length - 1
                        ? "truncate font-medium"
                        : "text-muted-foreground hidden max-w-32 min-w-0 truncate @min-[40rem]/toolbar:block"
                    }
                  >
                    {crumb.name}
                  </span>
                )}
              </Fragment>
            ))}
          </nav>
        </div>
        {showModeSwitch && supportsDocumentMode(mimeType) ? (
          <DocumentModeSwitch
            editorViewEnabled={editorViewEnabled}
            mode={documentMode}
            onModeChange={onDocumentModeChange}
          />
        ) : null}
        <Button
          aria-label="Reload"
          onClick={onReload}
          ref={reloadButtonRef}
          size="xs"
          title="Reload the embedded view if it appears blank"
          type="button"
          variant="outline"
        >
          <RefreshCw aria-hidden className="size-3.5" />
        </Button>
        <Button
          aria-label={copied ? "Link copied" : "Copy link"}
          onClick={handleCopy}
          size="xs"
          type="button"
          variant="outline"
        >
          {copied ? (
            <Check aria-hidden className="size-3.5" />
          ) : (
            <Copy aria-hidden className="size-3.5" />
          )}
          <span className="hidden @min-[40rem]/toolbar:inline">
            {copied ? "Copied" : "Copy link"}
          </span>
        </Button>
        {children}
        {openUrl === null ? null : (
          <Button asChild size="xs" variant="outline">
            <a
              aria-label={openLabel}
              href={openUrl}
              referrerPolicy="no-referrer"
              rel="noopener noreferrer"
              target="_blank"
            >
              <ExternalLink aria-hidden className="size-3.5" />
              <span className="hidden @min-[48rem]/toolbar:inline">
                {openLabel}
              </span>
            </a>
          </Button>
        )}
      </header>
      <p
        aria-live="polite"
        className={
          copyFailed
            ? "bg-muted shrink-0 border-b px-4 py-2 text-xs"
            : "sr-only"
        }
        role="status"
      >
        {copied ? "Link copied." : null}
        {copyFailed
          ? "Couldn’t copy the link. You can copy this page’s address from your browser."
          : null}
      </p>
    </>
  );
}

function DocumentModeSwitch({
  editorViewEnabled,
  mode,
  onModeChange,
}: {
  editorViewEnabled: boolean;
  mode: DocumentMode;
  onModeChange: (mode: DocumentMode) => void;
}) {
  return (
    <div
      aria-label="Document mode"
      className="bg-muted/40 inline-flex shrink-0 items-center gap-0.5 rounded-md border p-0.5"
      role="group"
    >
      <Button
        aria-pressed={mode === "preview"}
        onClick={() => onModeChange("preview")}
        size="xs"
        title="Preview this document"
        type="button"
        variant={mode === "preview" ? "secondary" : "ghost"}
      >
        <Eye
          aria-hidden
          className="hidden size-3.5 @min-[40rem]/toolbar:block"
        />
        Preview
      </Button>
      <Button
        aria-pressed={mode === "edit"}
        disabled={!editorViewEnabled}
        onClick={() => onModeChange("edit")}
        size="xs"
        title={
          editorViewEnabled
            ? "Open the editor view"
            : "You have view-only access to this document"
        }
        type="button"
        variant={mode === "edit" ? "secondary" : "ghost"}
      >
        <Pencil
          aria-hidden
          className="hidden size-3.5 @min-[40rem]/toolbar:block"
        />
        Edit
      </Button>
    </div>
  );
}
