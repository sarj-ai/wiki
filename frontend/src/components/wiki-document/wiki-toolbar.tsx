"use client";

import { Check, ChevronRight, Copy, ExternalLink, RefreshCw } from "lucide-react";
import { Fragment, useState } from "react";
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

  onReload: () => void;
}

const COPY_FEEDBACK_MS = 2000;

export function WikiToolbar({ fileId, mimeType, title, breadcrumb, onReload }: WikiToolbarProps) {
  const openUrl = openUrlFor(mimeType, fileId);
  const openLabel = openInGoogleLabel(mimeType);
  const [copied, setCopied] = useState(false);

  const trail: BreadcrumbItem[] = iframeShowsOwnTitle(mimeType)
    ? breadcrumb
    : [...breadcrumb, { id: fileId, name: title }];

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
        <a href={openUrl} target="_blank" rel="noopener noreferrer">
          <ExternalLink className="size-3.5" />
          {openLabel}
        </a>
      </Button>
    </header>
  );
}
