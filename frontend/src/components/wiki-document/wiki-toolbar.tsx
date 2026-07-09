"use client";

import { useQueryClient } from "@tanstack/react-query";
import {
  BookOpen,
  Check,
  ChevronRight,
  Copy,
  ExternalLink,
  MessageSquare,
  Pencil,
  RefreshCw,
} from "lucide-react";
import { Fragment, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { useDocViewerStore } from "@/components/wiki-document/doc-viewer-store";
import { iframeShowsOwnTitle, openInGoogleLabel, renderMimeIcon } from "@/lib/file-icons";
import { openUrlFor } from "@/lib/file-urls";
import { wikiKeys } from "@/lib/wiki-keys";
import { MIME_DOC, type BreadcrumbItem } from "@/types/wiki";

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
  const queryClient = useQueryClient();

  const isDoc = mimeType === MIME_DOC;
  const editing = useDocViewerStore((s) => s.editing);
  const showComments = useDocViewerStore((s) => s.showComments);
  const setEditing = useDocViewerStore((s) => s.setEditing);
  const setShowComments = useDocViewerStore((s) => s.setShowComments);

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

  const handleView = (): void => {
    // Post-edit content changed: drop the caches so View refetches.
    queryClient.removeQueries({ queryKey: wikiKeys.docHtml(fileId) });
    queryClient.removeQueries({ queryKey: wikiKeys.docComments(fileId) });
    setEditing(false);
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
      {isDoc && !editing && (
        <Button
          type="button"
          variant={showComments ? "secondary" : "outline"}
          size="xs"
          onClick={() => {
            setShowComments(!showComments);
          }}
          aria-pressed={showComments}
          aria-label={showComments ? "Hide comments" : "Show comments"}
        >
          <MessageSquare className="size-3.5" />
          Comments
        </Button>
      )}
      {isDoc &&
        (editing ? (
          <Button type="button" variant="outline" size="xs" onClick={handleView} aria-label="View">
            <BookOpen className="size-3.5" />
            View
          </Button>
        ) : (
          <Button
            type="button"
            variant="outline"
            size="xs"
            onClick={() => {
              setEditing(true);
            }}
            aria-label="Edit"
          >
            <Pencil className="size-3.5" />
            Edit
          </Button>
        ))}
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
