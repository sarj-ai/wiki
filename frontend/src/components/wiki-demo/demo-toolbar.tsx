"use client";

import {
  Check,
  ChevronRight,
  Copy,
  Download,
  ExternalLink,
  RefreshCw,
  Sparkles,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { renderMimeIcon } from "@/lib/file-icons";

import { demoAssetUrl, type DemoFile, demoFileFor, demoHref } from "./demo-data";

export function DemoToolbar({ file, onReload }: { file: DemoFile; onReload: () => void }) {
  const [copyStatus, setCopyStatus] = useState<"copied" | "failed" | "idle">("idle");
  useEffect(() => {
    if (copyStatus === "idle") return;
    const timer = window.setTimeout(() => setCopyStatus("idle"), 4_000);
    return () => window.clearTimeout(timer);
  }, [copyStatus]);

  const copyLink = () => {
    (async () => {
      await navigator.clipboard.writeText(new URL(demoHref(file), window.location.origin).href);
      setCopyStatus("copied");
    })().catch(() => setCopyStatus("failed"));
  };

  return (
    <>
      <header className="border-border bg-background text-foreground @container/toolbar flex min-h-11 shrink-0 items-center gap-1 border-b px-2 sm:gap-2 sm:px-4">
        <nav
          aria-label="Breadcrumb"
          className="flex min-w-0 flex-1 items-center gap-2 overflow-hidden text-sm"
          title={`Northstar / ${file.name}`}
        >
          <span aria-hidden className="shrink-0">
            {renderMimeIcon(file.mimeType, "size-4")}
          </span>
          <span className="text-muted-foreground hidden @min-[40rem]/toolbar:inline">
            Northstar
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
        <Button asChild size="sm" variant="outline">
          <a
            aria-label={`Download ${file.name}`}
            download={file.downloadFilename}
            href={demoAssetUrl(file.downloadFilename)}
          >
            <Download aria-hidden className="size-3.5" />
            <span className="hidden @min-[30rem]/toolbar:inline">Download</span>
          </a>
        </Button>
        {file.viewUrl ? (
          <Button asChild size="sm" variant="outline">
            <a
              aria-label="Open sample in Google Drive"
              href={file.viewUrl}
              referrerPolicy="no-referrer"
              rel="noopener noreferrer"
              target="_blank"
            >
              <ExternalLink aria-hidden className="size-3.5" />
              <span className="hidden @min-[40rem]/toolbar:inline">Open in Drive</span>
            </a>
          </Button>
        ) : null}
      </header>
      <p
        aria-live="polite"
        className={
          copyStatus === "failed" ? "bg-muted shrink-0 border-b px-4 py-2 text-xs" : "sr-only"
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

export function DemoFileContext({ file }: { file: DemoFile }) {
  const formattingExample = file.folder === "documents";
  return (
    <div className="bg-background flex shrink-0 flex-wrap items-center justify-between gap-2 border-b px-3 py-2.5 sm:px-5">
      <div className="min-w-0 flex-1">
        <p className="flex items-center gap-1.5 text-xs font-medium">
          {formattingExample ? (
            <Sparkles aria-hidden className="text-primary size-3.5" />
          ) : (
            <span aria-hidden>{renderMimeIcon(file.mimeType, "size-3.5")}</span>
          )}
          {formattingExample ? "Formatting example" : file.format}
        </p>
        <p className="text-muted-foreground mt-0.5 text-xs">
          {formattingExample ? "Same brief. Clear headings, lists, and tables." : file.description}
        </p>
      </div>
      {formattingExample ? (
        <div
          aria-label="Compare document formatting"
          className="bg-muted flex shrink-0 items-center rounded-md border p-0.5"
          role="group"
        >
          {[
            { id: "launch-brief-messy", label: "Before" },
            { id: "launch-brief-clean", label: "After" },
          ].map((variant) => (
            <Button
              asChild
              key={variant.id}
              size="sm"
              variant={file.id === variant.id ? "outline" : "ghost"}
            >
              <Link
                aria-current={file.id === variant.id ? "page" : undefined}
                href={demoHref(demoFileFor(variant.id))}
                prefetch={false}
              >
                {file.id === variant.id && variant.id === "launch-brief-clean" ? (
                  <Check aria-hidden className="size-3" />
                ) : null}
                {variant.label}
              </Link>
            </Button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
