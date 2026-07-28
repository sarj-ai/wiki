import {
  ClipboardList,
  FileText,
  FileType2,
  Folder,
  Image as LucideImage,
  Link2,
  Palette,
  Presentation,
  Table,
} from "lucide-react";
import type { ReactElement } from "react";

import { cn } from "@/lib/utils";
import {
  MIME_DOC,
  MIME_DRAWING,
  MIME_FOLDER,
  MIME_FORM,
  MIME_PDF,
  MIME_SHEET,
  MIME_SHORTCUT,
  MIME_SLIDE,
} from "@/types/wiki";

interface NodeIconShape {
  mimeType: string;
  shortcutTargetMimeType?: null | string | undefined;
}

const MIME_ICONS: Record<string, { icon: typeof FileText; tint: string }> = {
  [MIME_DOC]: { icon: FileText, tint: "text-icon-doc" },
  [MIME_DRAWING]: { icon: Palette, tint: "text-icon-drawing" },
  [MIME_FOLDER]: { icon: Folder, tint: "text-icon-folder" },
  [MIME_FORM]: { icon: ClipboardList, tint: "text-icon-form" },
  [MIME_PDF]: { icon: FileType2, tint: "text-icon-pdf" },
  [MIME_SHEET]: { icon: Table, tint: "text-icon-sheet" },
  [MIME_SLIDE]: { icon: Presentation, tint: "text-icon-slide" },
};

export function renderMimeIcon(mimeType: string, className?: string): ReactElement {
  const entry =
    MIME_ICONS[mimeType] ??
    (mimeType.startsWith("image/")
      ? { icon: LucideImage, tint: "text-icon-image" }
      : { icon: FileText, tint: "text-icon-generic" });
  const Icon = entry.icon;
  return <Icon className={cn(entry.tint, className)} />;
}

export function renderNodeIcon(node: NodeIconShape, className?: string): ReactElement {
  if (node.mimeType !== MIME_SHORTCUT) {
    return renderMimeIcon(node.mimeType, className);
  }

  const targetMime = node.shortcutTargetMimeType ?? MIME_FOLDER;
  return (
    <span className="relative inline-flex shrink-0">
      {renderMimeIcon(targetMime, className)}
      <Link2
        aria-hidden
        className="bg-background text-muted-foreground absolute -right-0.5 -bottom-0.5 size-2.5 rounded-full p-[1px]"
      />
    </span>
  );
}

export function openInGoogleLabel(mimeType: string): string {
  if (mimeType === MIME_DOC) return "Open in Google Docs";
  if (mimeType === MIME_SHEET) return "Open in Google Sheets";
  if (mimeType === MIME_SLIDE) return "Open in Google Slides";
  if (mimeType === MIME_DRAWING) return "Open in Google Drawings";
  if (mimeType === MIME_FORM) return "Open in Google Forms";
  return "Open in Drive";
}

export function isGoogleWorkspaceType(mimeType: string): boolean {
  return (
    mimeType === MIME_DOC ||
    mimeType === MIME_SHEET ||
    mimeType === MIME_SLIDE ||
    mimeType === MIME_DRAWING ||
    mimeType === MIME_FORM
  );
}

export function iframeShowsOwnTitle(mimeType: string): boolean {
  return (
    mimeType === MIME_DOC ||
    mimeType === MIME_SHEET ||
    mimeType === MIME_SLIDE ||
    mimeType === MIME_DRAWING
  );
}
