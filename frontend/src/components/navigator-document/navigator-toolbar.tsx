"use client";

import Link from "next/link";
import { ChevronRight, ExternalLink } from "lucide-react";
import { Fragment } from "react";
import { iframeShowsOwnTitle, openInGoogleLabel, renderMimeIcon } from "@/lib/file-icons";
import { openUrlFor } from "@/lib/file-urls";

interface NavigatorToolbarProps {
  fileId: string;
  mimeType: string;
  title: string;
  breadcrumb: string[];
}

export function NavigatorToolbar({ fileId, mimeType, title, breadcrumb }: NavigatorToolbarProps) {
  const openUrl = openUrlFor(mimeType, fileId);
  const openLabel = openInGoogleLabel(mimeType);
  // Docs/Sheets/Slides/Drawings always render their own title inside
  // the iframe, so duplicating it in our breadcrumb is pure visual
  // noise. For every other type (images, PDFs, until confirmed) we
  // append the filename so the user can still identify the file.
  const trail = iframeShowsOwnTitle(mimeType) ? breadcrumb : [...breadcrumb, title];

  return (
    <header className="border-border bg-background text-foreground flex items-center gap-2 border-b px-4 py-2">
      <div className="flex min-w-0 flex-1 items-center gap-2">
        <span className="shrink-0">{renderMimeIcon(mimeType, "size-4")}</span>
        <nav
          aria-label="Breadcrumb"
          className="flex min-w-0 items-center gap-1 text-sm"
          title={[...trail, title].join(" / ")}
        >
          {trail.map((crumb, index) => {
            const isLast = index === trail.length - 1;
            return (
              <Fragment key={`${crumb}-${index}`}>
                {index > 0 && <ChevronRight className="text-muted-foreground size-3.5 shrink-0" />}
                <span
                  className={
                    isLast ? "truncate font-medium" : "text-muted-foreground shrink-0 truncate"
                  }
                >
                  {crumb}
                </span>
              </Fragment>
            );
          })}
        </nav>
      </div>
      <Link
        href={openUrl}
        target="_blank"
        rel="noopener noreferrer"
        className="border-border hover:bg-accent hover:text-accent-foreground inline-flex shrink-0 items-center gap-1.5 rounded-md border px-2.5 py-1.5 text-xs font-medium"
      >
        <ExternalLink className="size-3.5" />
        {openLabel}
      </Link>
    </header>
  );
}
