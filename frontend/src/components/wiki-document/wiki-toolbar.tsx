"use client";

import { Check, ChevronRight, Copy, ExternalLink, RefreshCw, Sparkles } from "lucide-react";
import { Fragment, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  displayFileName,
  iframeShowsOwnTitle,
  openInGoogleLabel,
  renderMimeIcon,
} from "@/lib/file-icons";
import { openUrlFor } from "@/lib/file-urls";
import type { BreadcrumbItem } from "@/types/wiki";
import { MIME_DOC, MIME_MARKDOWN } from "@/types/wiki";

import { DocumentPolishDialog } from "./document-polish-dialog";
import { ReadOnlyShortLink } from "./read-only-short-link";
import { ReorganizeDocument } from "./reorganize-document";
import { ShortLinkDialog } from "./short-link-dialog";

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

  title: string;
}

const COPY_FEEDBACK_MS = 2000;

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
  title,
}: WikiToolbarProps) {
  const openUrl = openUrlFor(mimeType, fileId);
  const openLabel = openInGoogleLabel(mimeType);
  const [copied, setCopied] = useState(false);
  const [polishOpen, setPolishOpen] = useState(false);
  const displayTitle = displayFileName(title, mimeType);

  const trail: BreadcrumbItem[] = iframeShowsOwnTitle(mimeType)
    ? breadcrumb
    : [...breadcrumb, { id: fileId, name: displayTitle }];

  const handleCopy = (): void => {
    void navigator.clipboard
      .writeText(window.location.href)
      .then(() => {
        setCopied(true);
        window.setTimeout(() => {
          setCopied(false);
        }, COPY_FEEDBACK_MS);
      })
      .catch((err: unknown) => {
        const message = err instanceof Error ? err.message : "Failed to copy link";
        toast.error(message);
      });
  };

  return (
    <header className="border-border bg-background text-foreground flex h-10 shrink-0 items-center gap-1 overflow-hidden border-b pr-4 pl-12 sm:gap-2 md:pl-4">
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
                {index > 0 && <ChevronRight className="text-muted-foreground size-3.5 shrink-0" />}
                <span
                  className={
                    isLast ? "truncate font-medium" : "text-muted-foreground shrink-0 truncate"
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
        <span className="hidden sm:inline">{copied ? "Copied" : "Copy link"}</span>
      </Button>
      {authority === "resolved" &&
      modifiedTime !== undefined &&
      (mimeType === MIME_DOC || mimeType === MIME_MARKDOWN) ? (
        <>
          <Button
            aria-label="Polish document"
            onClick={() => {
              setPolishOpen(true);
            }}
            size="xs"
            type="button"
            variant="outline"
          >
            <Sparkles className="size-3.5" />
            <span className="hidden sm:inline">Polish</span>
          </Button>
          <DocumentPolishDialog
            key={fileId}
            onOpenChange={setPolishOpen}
            open={polishOpen}
            source={{
              expectedModifiedTime: modifiedTime,
              fileId,
              kind: "drive_file",
              name: displayTitle,
            }}
          />
        </>
      ) : null}
      <div className="size-6 shrink-0 sm:w-24">
        {authority === "resolved" ? (
          <ReorganizeDocument
            driveId={driveId}
            fileId={fileId}
            fileName={displayTitle}
            parentFolderId={parentFolderId}
          />
        ) : (
          <span aria-hidden className="bg-muted block size-full animate-pulse rounded-md" />
        )}
      </div>
      <div className="size-6 shrink-0 sm:w-24">
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
        <a href={openUrl} rel="noopener noreferrer" target="_blank">
          <ExternalLink className="size-3.5" />
          <span className="hidden md:inline">{openLabel}</span>
        </a>
      </Button>
    </header>
  );
}
