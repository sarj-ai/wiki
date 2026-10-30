"use client";

import { ArrowUpRight, Clock3 } from "lucide-react";
import Link from "next/link";

import { Button } from "@/components/ui/button";
import { useDocumentIntent } from "@/components/wiki-sidebar/use-document-intent";
import { displayFileName, renderMimeIcon } from "@/lib/file-icons";
import { openUrlFor } from "@/lib/file-urls";
import { MIME_FORM } from "@/types/wiki";

export interface RecentPage {
  driveName: null | string;
  id: string;
  mimeType: string;
  name: string;
}

interface RecentPagesProps {
  error?: boolean;
  loading?: boolean;
  loadingCount?: number;
  onRetry?: () => void;
  pages: RecentPage[];
}

export function RecentPages({
  error = false,
  loading = false,
  loadingCount = 5,
  onRetry,
  pages,
}: RecentPagesProps) {
  return (
    <div className="h-full overflow-y-auto">
      <section
        aria-labelledby="recent-pages-heading"
        className="mx-auto flex min-h-full w-full max-w-3xl flex-col px-4 py-8 sm:px-8 md:py-24"
      >
        <div className="bg-muted mb-5 flex size-12 items-center justify-center rounded-xl">
          <Clock3 aria-hidden className="text-muted-foreground size-6" />
        </div>
        <h1
          className="text-foreground text-2xl font-semibold tracking-tight"
          id="recent-pages-heading"
        >
          Recent pages
        </h1>
        <p className="text-muted-foreground mt-1 text-sm">Pick up where you left off.</p>
        <div
          aria-busy={loading}
          className="bg-card mt-6 overflow-hidden rounded-xl border shadow-xs"
        >
          {loading ? (
            <RecentPagesSkeleton count={loadingCount} />
          ) : error ? (
            <div className="space-y-3 p-6">
              <p className="text-sm">Your recent pages could not be loaded.</p>
              {onRetry ? (
                <Button onClick={onRetry} size="sm" variant="outline">
                  Try again
                </Button>
              ) : null}
            </div>
          ) : pages.length === 0 ? (
            <div className="p-6">
              <p className="text-sm font-medium">Your recent pages will appear here</p>
              <p className="text-muted-foreground mt-1 text-sm">
                Open a page from the sidebar or search to get started.
              </p>
            </div>
          ) : (
            <ul aria-label="Recent pages">
              {pages.slice(0, 5).map((page) => (
                <RecentPageRow key={page.id} page={page} />
              ))}
            </ul>
          )}
        </div>
      </section>
    </div>
  );
}

function RecentPagesSkeleton({ count }: { count: number }) {
  const rows = Math.max(0, Math.min(5, count));
  return (
    <>
      <p className={rows > 0 ? "sr-only" : "text-muted-foreground p-6 text-sm"} role="status">
        Loading recent pages…
      </p>
      <div aria-hidden="true">
        {Array.from({ length: rows }, (_, index) => (
          <div
            className="flex min-h-20 items-center gap-3 border-b p-4 last:border-b-0"
            key={index}
          >
            <span className="bg-muted size-10 shrink-0 rounded-lg" />
            <span className="min-w-0 flex-1 space-y-2">
              <span className="bg-muted block h-4 w-2/3 rounded-sm" />
              <span className="bg-muted block h-3 w-1/3 rounded-sm" />
            </span>
            <span className="bg-muted size-4 shrink-0 rounded-sm" />
          </div>
        ))}
      </div>
    </>
  );
}

function RecentPageRow({ page }: { page: RecentPage }) {
  const isForm = page.mimeType === MIME_FORM;
  const name = displayFileName(page.name, page.mimeType);
  const intent = useDocumentIntent({
    disabled: isForm,
    fileId: page.id,
  });
  const linkClassName =
    "hover:bg-accent/60 focus-visible:ring-ring flex min-h-20 items-center gap-3 p-4 outline-none focus-visible:ring-2 focus-visible:ring-inset";
  const contents = (
    <>
      <span className="bg-background flex size-10 shrink-0 items-center justify-center rounded-lg border">
        {renderMimeIcon(page.mimeType, "size-5")}
      </span>
      <span className="min-w-0 flex-1">
        <span className="line-clamp-2 text-sm font-medium wrap-break-word sm:block sm:truncate">
          {name}
        </span>
        <span className="text-muted-foreground mt-1 block truncate text-xs">
          {page.driveName ?? "My Drive"}
        </span>
      </span>
      <ArrowUpRight
        aria-hidden={!isForm}
        aria-label={isForm ? "Opens in a new tab" : undefined}
        className="text-muted-foreground size-4 shrink-0"
      />
    </>
  );
  return (
    <li className="border-b last:border-b-0">
      {isForm ? (
        <a
          className={linkClassName}
          href={openUrlFor(page.mimeType, page.id)}
          rel="noopener noreferrer"
          target="_blank"
          title={`${name} — opens in a new tab`}
        >
          {contents}
        </a>
      ) : (
        <Link
          {...intent}
          className={linkClassName}
          href={`/d/${encodeURIComponent(page.id)}`}
          prefetch={false}
          title={name}
        >
          {contents}
        </Link>
      )}
    </li>
  );
}
