import type { ReactElement } from "react";

import {
  ClipboardList,
  FileType2,
  FileText,
  Folder,
  Image as LucideImage,
  Link2,
  Palette,
  Presentation,
  Table,
} from "lucide-react";

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

// Lucide outlines tinted per Workspace type via the --icon-* tokens in
// globals.css (Google brand hues, dark-mode adjusted). Solid outlines
// read cleanly at 16px — detailed mini-scenes with gradients got muddy
// at that size.
const MIME_ICONS: Record<string, { icon: typeof FileText; tint: string }> = {
  [MIME_FOLDER]: { icon: Folder, tint: "text-icon-folder" },
  [MIME_DOC]: { icon: FileText, tint: "text-icon-doc" },
  [MIME_SHEET]: { icon: Table, tint: "text-icon-sheet" },
  [MIME_SLIDE]: { icon: Presentation, tint: "text-icon-slide" },
  [MIME_DRAWING]: { icon: Palette, tint: "text-icon-drawing" },
  [MIME_FORM]: { icon: ClipboardList, tint: "text-icon-form" },
  [MIME_PDF]: { icon: FileType2, tint: "text-icon-pdf" },
};

// Renders the icon for a mime type as a JSX element. The map holds
// module-scope component references, so aliasing one at render time
// doesn't trip the React Compiler "components created during render"
// lint rule (same idiom as ERROR_CONFIG in login/page.tsx).
export function renderMimeIcon(mimeType: string, className?: string): ReactElement {
  const entry =
    MIME_ICONS[mimeType] ??
    (mimeType.startsWith("image/")
      ? { icon: LucideImage, tint: "text-icon-image" }
      : { icon: FileText, tint: "text-icon-generic" });
  const Icon = entry.icon;
  return <Icon className={cn(entry.tint, className)} />;
}

// Shortcut nodes carry their target's mime in ``shortcutTargetMimeType``
// — the rendered icon should match the *target*, since that's the file
// the user will actually open. The Link2 overlay then signals "this is
// a pointer, not the original".
interface NodeIconShape {
  mimeType: string;
  shortcutTargetMimeType?: string | null | undefined;
}

export function renderNodeIcon(node: NodeIconShape, className?: string): ReactElement {
  if (node.mimeType !== MIME_SHORTCUT) {
    return renderMimeIcon(node.mimeType, className);
  }
  // Fall back to the folder icon if the target mime is missing — should
  // never happen post-resolver, but keeps the render defensive.
  const targetMime = node.shortcutTargetMimeType ?? MIME_FOLDER;
  return (
    <span className="relative inline-flex shrink-0">
      {renderMimeIcon(targetMime, className)}
      <Link2
        className="bg-background text-muted-foreground absolute -right-0.5 -bottom-0.5 size-2.5 rounded-full p-[1px]"
        aria-hidden
      />
    </span>
  );
}

/** Human-readable label for the Open-in-Google button per type. */
export function openInGoogleLabel(mimeType: string): string {
  if (mimeType === MIME_DOC) return "Open in Google Docs";
  if (mimeType === MIME_SHEET) return "Open in Google Sheets";
  if (mimeType === MIME_SLIDE) return "Open in Google Slides";
  if (mimeType === MIME_DRAWING) return "Open in Google Drawings";
  if (mimeType === MIME_FORM) return "Open in Google Forms";
  return "Open in Drive";
}

/** True when the file is a Google Workspace type with full editing inside the iframe. */
export function isGoogleWorkspaceType(mimeType: string): boolean {
  return (
    mimeType === MIME_DOC ||
    mimeType === MIME_SHEET ||
    mimeType === MIME_SLIDE ||
    mimeType === MIME_DRAWING ||
    mimeType === MIME_FORM
  );
}

/**
 * True when the file's iframe always renders its own filename header,
 * so Sarj Wiki toolbar shouldn't duplicate it in the breadcrumb.
 * Confirmed against the live Google embed for each listed type —
 * add Forms/PDF/other as they're checked.
 */
export function iframeShowsOwnTitle(mimeType: string): boolean {
  return (
    mimeType === MIME_DOC ||
    mimeType === MIME_SHEET ||
    mimeType === MIME_SLIDE ||
    mimeType === MIME_DRAWING
  );
}
