import type { ReactNode } from "react";

import { cn } from "@/lib/utils";
import { MIME_PPTX, MIME_SHEET, MIME_SLIDE } from "@/types/wiki";

export function DrivePreviewSurface({
  children,
  mimeType,
}: {
  children: ReactNode;
  mimeType: string;
}) {
  const inset =
    mimeType === MIME_SHEET ||
    mimeType === MIME_SLIDE ||
    mimeType === MIME_PPTX ||
    mimeType ===
      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";

  return (
    <div className={cn("absolute inset-0", inset && "bg-muted/30 p-2 sm:p-4")}>
      <div
        className={cn(
          "relative size-full min-h-0 min-w-0",
          inset && "bg-background overflow-hidden rounded-lg border shadow-sm",
        )}
      >
        {children}
      </div>
    </div>
  );
}
