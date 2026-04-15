"use client";

import Link from "next/link";
import { ExternalLink } from "lucide-react";
import { renderMimeIcon, openInGoogleLabel } from "@/lib/file-icons";
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

  return (
    <header className="border-border bg-background flex items-center gap-2 border-b px-4 py-2">
      <div className="flex min-w-0 flex-1 items-center gap-2">
        <span className="text-muted-foreground shrink-0">{renderMimeIcon(mimeType, "size-4")}</span>
        <div className="min-w-0 flex-1">
          {breadcrumb.length > 0 && (
            <div className="text-muted-foreground truncate text-xs">{breadcrumb.join(" / ")}</div>
          )}
          <div className="truncate text-sm font-medium">{title}</div>
        </div>
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
