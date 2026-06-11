"use client";

import { Check, ChevronRight, Copy, ExternalLink, RefreshCw } from "lucide-react";
import Link from "next/link";
import { Fragment, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import type { BreadcrumbItem } from "@/types/wiki";

import { Button } from "@/components/ui/button";
import { iframeShowsOwnTitle, openInGoogleLabel, renderMimeIcon } from "@/lib/file-icons";
import { openUrlFor } from "@/lib/file-urls";

interface WikiToolbarProps {
  fileId: string;
  mimeType: string;
  title: string;
  breadcrumb: BreadcrumbItem[];
  // Recovery affordance for Google's embedded editor going black/blank
  // after long browser-tab backgrounding. The page bumps the iframe's
  // ``reloadKey`` in response, which forces a fresh editor session.
  onReload: () => void;
}

// How long the "Copied" check-mark + label stay before the button
// reverts to the default Copy icon. Two seconds is the sweet spot
// users have learned from GitHub / Linear / Notion: long enough to
// register the confirmation, short enough that re-copying doesn't
// feel sticky.
const COPY_FEEDBACK_MS = 2000;

export function WikiToolbar({ fileId, mimeType, title, breadcrumb, onReload }: WikiToolbarProps) {
  const openUrl = openUrlFor(mimeType, fileId);
  const openLabel = openInGoogleLabel(mimeType);
  const [copied, setCopied] = useState(false);
  // Cleared on unmount so a pending revert can't fire into a dead
  // component after rapid navigation.
  const copyTimerRef = useRef<number | null>(null);
  useEffect(
    () => () => {
      if (copyTimerRef.current !== null) window.clearTimeout(copyTimerRef.current);
    },
    [],
  );
  // Docs/Sheets/Slides/Drawings always render their own title inside
  // the iframe, so duplicating it in our breadcrumb is pure visual
  // noise. For every other type (images, PDFs, until confirmed) we
  // append the filename so the user can still identify the file.
  const trail: BreadcrumbItem[] = iframeShowsOwnTitle(mimeType)
    ? breadcrumb
    : [...breadcrumb, { id: fileId, name: title }];

  // Copy the *Sarj Wiki* URL (the current page) rather than the raw
  // Drive URL: that way pasting the link into Slack/email keeps
  // recipients inside Sarj Wiki's iframe-embedded view, with the
  // sidebar context intact. Using ``window.location.href`` instead of
  // hand-rolling ``/d/<fileId>`` lets any future query params or hash
  // anchors come along for free.
  const handleCopy = (): void => {
    void navigator.clipboard
      .writeText(window.location.href)
      .then(() => {
        setCopied(true);
        if (copyTimerRef.current !== null) window.clearTimeout(copyTimerRef.current);
        copyTimerRef.current = window.setTimeout(() => {
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
        type="button"
        variant="outline"
        size="xs"
        onClick={onReload}
        aria-label="Reload"
        title="Reload the embedded view if it appears blank"
      >
        <RefreshCw className="size-3.5" />
      </Button>
      <Button
        type="button"
        variant="outline"
        size="xs"
        onClick={handleCopy}
        aria-label={copied ? "Link copied" : "Copy link"}
      >
        {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
        {copied ? "Copied" : "Copy link"}
      </Button>
      <Button asChild variant="outline" size="xs">
        <Link href={openUrl} target="_blank" rel="noopener noreferrer">
          <ExternalLink className="size-3.5" />
          {openLabel}
        </Link>
      </Button>
    </header>
  );
}
