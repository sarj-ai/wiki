"use client";

import {
  Check,
  ChevronRight,
  Copy,
  Download,
  ExternalLink,
  RefreshCw,
} from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { renderMimeIcon } from "@/lib/file-icons";

import { DEMO_WORKSPACE_NAME, type DemoFile, demoHref } from "./demo-data";
import { demoDownloadUrl, demoViewUrl } from "./demo-file-urls";

export function DemoToolbar({
  file,
  onReload,
}: {
  file: DemoFile;
  onReload: () => void;
}) {
  const downloadUrl = demoDownloadUrl(file);
  const viewUrl = demoViewUrl(file);
  const [copyStatus, setCopyStatus] = useState<"copied" | "failed" | "idle">(
    "idle",
  );
  useEffect(() => {
    if (copyStatus === "idle") return;
    const timer = window.setTimeout(() => setCopyStatus("idle"), 4_000);
    return () => window.clearTimeout(timer);
  }, [copyStatus]);

  const copyLink = () => {
    (async () => {
      await navigator.clipboard.writeText(
        new URL(demoHref(file), window.location.origin).href,
      );
      setCopyStatus("copied");
    })().catch(() => setCopyStatus("failed"));
  };

  return (
    <>
      <header className="border-border bg-background text-foreground @container/toolbar flex min-h-11 shrink-0 items-center gap-1 border-b px-2 sm:gap-2 sm:px-4">
        <nav
          aria-label="Breadcrumb"
          className="flex min-w-0 flex-1 items-center gap-2 overflow-hidden text-sm"
          title={`${DEMO_WORKSPACE_NAME} / ${file.name}`}
        >
          <span aria-hidden className="shrink-0">
            {renderMimeIcon(file.mimeType, "size-4")}
          </span>
          <span className="text-muted-foreground hidden @min-[40rem]/toolbar:inline">
            {DEMO_WORKSPACE_NAME}
          </span>
          <ChevronRight
            aria-hidden
            className="text-muted-foreground hidden size-3.5 shrink-0 @min-[40rem]/toolbar:block"
          />
          <h1 className="truncate font-medium">{file.name}</h1>
        </nav>
        <Button
          aria-label="Reload preview"
          onClick={onReload}
          size="icon-sm"
          title="Reload preview"
          variant="outline"
        >
          <RefreshCw aria-hidden className="size-3.5" />
        </Button>
        <Button
          aria-label={copyStatus === "copied" ? "Link copied" : "Copy link"}
          onClick={copyLink}
          size="icon-sm"
          title="Copy link"
          variant="outline"
        >
          {copyStatus === "copied" ? (
            <Check aria-hidden className="size-3.5" />
          ) : (
            <Copy aria-hidden className="size-3.5" />
          )}
        </Button>
        {downloadUrl === null ? null : (
          <Button asChild size="sm" variant="outline">
            <a
              aria-label={`Download ${file.name}`}
              download={file.downloadFilename}
              href={downloadUrl}
              referrerPolicy="no-referrer"
              rel="noopener noreferrer"
              target="_blank"
            >
              <Download aria-hidden className="size-3.5" />
              <span className="hidden @min-[30rem]/toolbar:inline">
                Download
              </span>
            </a>
          </Button>
        )}
        {viewUrl === null ? null : (
          <Button asChild size="sm" variant="outline">
            <a
              aria-label="Open sample in Google Drive"
              href={viewUrl}
              referrerPolicy="no-referrer"
              rel="noopener noreferrer"
              target="_blank"
            >
              <ExternalLink aria-hidden className="size-3.5" />
              <span className="hidden @min-[40rem]/toolbar:inline">
                Open in Drive
              </span>
            </a>
          </Button>
        )}
      </header>
      <p
        aria-live="polite"
        className={
          copyStatus === "failed"
            ? "bg-muted shrink-0 border-b px-4 py-2 text-xs"
            : "sr-only"
        }
        role="status"
      >
        {copyStatus === "copied" ? "Public demo link copied." : null}
        {copyStatus === "failed"
          ? "Couldn’t copy the link. You can copy this page’s address from your browser."
          : null}
      </p>
    </>
  );
}
