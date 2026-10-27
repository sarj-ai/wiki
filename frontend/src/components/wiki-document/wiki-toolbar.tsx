"use client";

import { Check, ChevronRight, Copy, ExternalLink, RefreshCw, Sparkles } from "lucide-react";
import { Fragment } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { useDeferredDialog } from "@/hooks/use-deferred-dialog";
import {
  displayFileName,
  iframeShowsOwnTitle,
  openInGoogleLabel,
  renderMimeIcon,
} from "@/lib/file-icons";
import { openUrlFor } from "@/lib/file-urls";
import type { BreadcrumbItem } from "@/types/wiki";
import { MIME_DOC, MIME_DOCX, MIME_MARKDOWN, MIME_PPTX, MIME_SLIDE } from "@/types/wiki";

import { LazyDocumentPolishDialog } from "./lazy-document-polish-dialog";
import { ReadOnlyShortLink } from "./read-only-short-link";
import { ReorganizeDocument } from "./reorganize-document";
import { ShortLinkDialog } from "./short-link-dialog";
import { useCopyFeedback } from "./use-copy-feedback";

interface WikiToolbarProps {
  authority?: "pending" | "resolved";
  breadcrumb: BreadcrumbItem[];
  canEdit: boolean;
  driveId: string | undefined;
  fileId: string;
  mimeType: string;
  modifiedTime?: string;
  onReload: () => void;
  parentFolderId: string | undefined;
  refreshModifiedTime?: () => Promise<string>;

  title: string;
}

export function WikiToolbar({
  authority = "resolved",
  breadcrumb,
  canEdit,
  driveId,
  fileId,
  mimeType,
  modifiedTime,
  onReload,
  parentFolderId,
  refreshModifiedTime,
  title,
}: WikiToolbarProps) {
  const openUrl = openUrlFor(mimeType, fileId);
  const openLabel = openInGoogleLabel(mimeType);
  const { copied, showCopied } = useCopyFeedback(fileId);
  const polishDialog = useDeferredDialog();
  const displayTitle = displayFileName(title, mimeType);

  const trail: BreadcrumbItem[] = iframeShowsOwnTitle(mimeType)
    ? breadcrumb
    : [...breadcrumb, { id: fileId, name: displayTitle }];

  const handleCopy = (): void => {
    void (async () => {
      await navigator.clipboard.writeText(window.location.href);
      showCopied();
    })().catch((err: unknown) => {
      const message = err instanceof Error ? err.message : "Failed to copy link";
      toast.error(message);
    });
  };

  return (
    <header className="border-border bg-background text-foreground @container/toolbar flex h-10 shrink-0 items-center gap-1 overflow-hidden border-b px-4 sm:gap-2">
      <div className="flex min-w-0 flex-1 items-center gap-2 overflow-hidden">
        <span className="shrink-0">{renderMimeIcon(mimeType, "size-4")}</span>
        <nav
          aria-label="Breadcrumb"
          className="flex min-w-0 items-center gap-1 text-sm"
          title={trail.map((c) => c.name).join(" / ")}
        >
          {trail.map((crumb, index) => {
            const isLast = index === trail.length - 1;
            return (
              <Fragment key={crumb.id}>
                {index > 0 && (
                  <ChevronRight className="text-muted-foreground hidden size-3.5 shrink-0 @min-[40rem]/toolbar:block" />
                )}
                <span
                  className={
                    isLast
                      ? "truncate font-medium"
                      : "text-muted-foreground hidden max-w-32 min-w-0 truncate @min-[40rem]/toolbar:block"
                  }
                >
                  {crumb.name}
                </span>
              </Fragment>
            );
          })}
        </nav>
      </div>
      <Button
        aria-label="Reload"
        onClick={onReload}
        size="xs"
        title="Reload the embedded view if it appears blank"
        type="button"
        variant="outline"
      >
        <RefreshCw className="size-3.5" />
      </Button>
      <Button
        aria-label={copied ? "Link copied" : "Copy link"}
        onClick={handleCopy}
        size="xs"
        type="button"
        variant="outline"
      >
        {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
        <span className="hidden @min-[40rem]/toolbar:inline">
          {copied ? "Copied" : "Copy link"}
        </span>
      </Button>
      {authority === "resolved" &&
      modifiedTime !== undefined &&
      (mimeType === MIME_DOC ||
        mimeType === MIME_DOCX ||
        mimeType === MIME_MARKDOWN ||
        mimeType === MIME_SLIDE ||
        mimeType === MIME_PPTX) ? (
        <>
          <Button
            aria-label={
              mimeType === MIME_SLIDE || mimeType === MIME_PPTX
                ? "Polish presentation"
                : "Polish document"
            }
            onClick={() => {
              polishDialog.onOpenChange(true);
            }}
            size="xs"
            type="button"
            variant="outline"
          >
            <Sparkles className="size-3.5" />
            <span className="hidden @min-[40rem]/toolbar:inline">Polish</span>
          </Button>
          {polishDialog.mounted ? (
            <LazyDocumentPolishDialog
              key={fileId}
              onInPlaceComplete={onReload}
              onOpenChange={polishDialog.onOpenChange}
              open={polishDialog.open}
              source={{
                allowInPlace: canEdit && mimeType === MIME_DOC,
                expectedModifiedTime: modifiedTime,
                fileId,
                kind: "drive_file",
                mimeType,
                name: displayTitle,
                refreshExpectedModifiedTime:
                  refreshModifiedTime ??
                  (() =>
                    Promise.reject(
                      new Error("Couldn’t verify the document’s latest revision. Please reload."),
                    )),
              }}
            />
          ) : null}
        </>
      ) : null}
      {authority === "resolved" ? (
        canEdit ? (
          <div className="size-6 shrink-0 @min-[40rem]/toolbar:w-24">
            <ReorganizeDocument
              driveId={driveId}
              fileId={fileId}
              fileName={displayTitle}
              parentFolderId={parentFolderId}
            />
          </div>
        ) : null
      ) : (
        <div className="size-6 shrink-0 @min-[40rem]/toolbar:w-24">
          <span aria-hidden className="bg-muted block size-full animate-pulse rounded-md" />
        </div>
      )}
      <div className="size-6 shrink-0 @min-[40rem]/toolbar:w-24">
        {authority === "resolved" ? (
          canEdit ? (
            <ShortLinkDialog fileId={fileId} />
          ) : (
            <ReadOnlyShortLink fileId={fileId} />
          )
        ) : (
          <span aria-hidden className="bg-muted block size-full animate-pulse rounded-md" />
        )}
      </div>
      <Button asChild size="xs" variant="outline">
        <a aria-label={openLabel} href={openUrl} rel="noopener noreferrer" target="_blank">
          <ExternalLink className="size-3.5" />
          <span className="hidden @min-[48rem]/toolbar:inline">{openLabel}</span>
        </a>
      </Button>
    </header>
  );
}
