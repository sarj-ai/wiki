"use client";

import { Check, ChevronRight, Copy, ExternalLink, RefreshCw } from "lucide-react";
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
import { MIME_DOC } from "@/types/wiki";

import { ReadOnlyShortLink } from "./read-only-short-link";
import { ReorganizeDocument } from "./reorganize-document";
import { ShortLinkDialog } from "./short-link-dialog";

interface WikiToolbarProps {
  breadcrumb: BreadcrumbItem[];
  canEdit: boolean;
  driveId: string | undefined;
  fileId: string;
  mimeType: string;
  onReload: () => void;
  parentFolderId: string | undefined;

  title: string;
}

const COPY_FEEDBACK_MS = 2000;

export function WikiToolbar({
  breadcrumb,
  canEdit,
  driveId,
  fileId,
  mimeType,
  onReload,
  parentFolderId,
  title,
}: WikiToolbarProps) {
  const openUrl = openUrlFor(mimeType, fileId);
  const openLabel = openInGoogleLabel(mimeType);
  const [copied, setCopied] = useState(false);
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
    <header className="border-border bg-background text-foreground flex items-center gap-2 border-b px-4 py-2">
      <div className="flex min-w-0 flex-1 items-center gap-2">
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
        {copied ? "Copied" : "Copy link"}
      </Button>
      {canEdit && mimeType === MIME_DOC ? (
        <ReorganizeDocument
          driveId={driveId}
          fileId={fileId}
          fileName={displayTitle}
          parentFolderId={parentFolderId}
        />
      ) : null}
      {canEdit ? <ShortLinkDialog fileId={fileId} /> : <ReadOnlyShortLink fileId={fileId} />}
      <Button asChild size="xs" variant="outline">
        <a href={openUrl} rel="noopener noreferrer" target="_blank">
          <ExternalLink className="size-3.5" />
          {openLabel}
        </a>
      </Button>
    </header>
  );
}
