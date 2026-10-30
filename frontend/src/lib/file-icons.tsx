import {
  Archive,
  Braces,
  ClipboardList,
  FileAudio,
  FileCode2,
  FileText,
  FileType2,
  FileVideo,
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
  MIME_HTML,
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
  "application/json": { icon: Braces, tint: "text-icon-generic" },
  "application/msword": { icon: FileText, tint: "text-icon-doc" },
  "application/vnd.ms-excel": { icon: Table, tint: "text-icon-sheet" },
  "application/vnd.ms-powerpoint": { icon: Presentation, tint: "text-icon-slide" },
  "application/vnd.openxmlformats-officedocument.presentationml.presentation": {
    icon: Presentation,
    tint: "text-icon-slide",
  },
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {
    icon: Table,
    tint: "text-icon-sheet",
  },
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document": {
    icon: FileText,
    tint: "text-icon-doc",
  },
  "application/x-zip-compressed": { icon: Archive, tint: "text-icon-generic" },
  "application/zip": { icon: Archive, tint: "text-icon-generic" },
  [MIME_DOC]: { icon: FileText, tint: "text-icon-doc" },
  [MIME_DRAWING]: { icon: Palette, tint: "text-icon-drawing" },
  [MIME_FOLDER]: { icon: Folder, tint: "text-icon-folder" },
  [MIME_FORM]: { icon: ClipboardList, tint: "text-icon-form" },
  [MIME_HTML]: { icon: FileCode2, tint: "text-icon-form" },
  [MIME_PDF]: { icon: FileType2, tint: "text-icon-pdf" },
  [MIME_SHEET]: { icon: Table, tint: "text-icon-sheet" },
  [MIME_SLIDE]: { icon: Presentation, tint: "text-icon-slide" },
  "text/csv": { icon: Table, tint: "text-icon-sheet" },
  "text/plain": { icon: FileText, tint: "text-icon-generic" },
};

export function renderMimeIcon(mimeType: string, className?: string): ReactElement {
  const entry = MIME_ICONS[mimeType] ?? fallbackIcon(mimeType);
  const Icon = entry.icon;
  return <Icon className={cn(entry.tint, className)} />;
}

/** Keeps storage names intact while removing redundant format suffixes in UI labels. */
export function displayFileName(name: string, mimeType: string): string {
  if (mimeType !== MIME_HTML) return name;
  return name.replace(/\.html?$/iu, "");
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

const FALLBACK_ICONS = {
  audio: { icon: FileAudio, tint: "text-icon-generic" },
  file: { icon: FileText, tint: "text-icon-generic" },
  image: { icon: LucideImage, tint: "text-icon-image" },
  video: { icon: FileVideo, tint: "text-icon-slide" },
} as const;

function fallbackIcon(mimeType: string): { icon: typeof FileText; tint: string } {
  if (mimeType.startsWith("image/")) return FALLBACK_ICONS.image;
  if (mimeType.startsWith("audio/")) return FALLBACK_ICONS.audio;
  if (mimeType.startsWith("video/")) return FALLBACK_ICONS.video;
  return FALLBACK_ICONS.file;
}
