import type { ComponentProps } from "react";

import { DrivePreviewSurface } from "@/components/ui/drive-preview-surface";
import { cn } from "@/lib/utils";

type DriveDocumentIframeProps = Pick<
  ComponentProps<"iframe">,
  | "allow"
  | "allowFullScreen"
  | "aria-hidden"
  | "className"
  | "onLoad"
  | "ref"
  | "referrerPolicy"
  | "sandbox"
  | "tabIndex"
> & { mimeType: string; src: string; title: string };

export function DriveDocumentIframe({
  mimeType,
  className,
  sandbox,
  src,
  title,
  ...props
}: DriveDocumentIframeProps) {
  return (
    <DrivePreviewSurface mimeType={mimeType}>
      <iframe
        {...props}
        sandbox={sandbox}
        src={src}
        title={title}
        className={cn("block size-full border-0", className)}
      />
    </DrivePreviewSurface>
  );
}
