import {
  ClipboardList,
  FileType2,
  FileText,
  Folder,
  Image as LucideImage,
  Palette,
  Presentation,
  Table,
} from "lucide-react";
import type { ReactElement } from "react";
import {
  MIME_DOC,
  MIME_DRAWING,
  MIME_FOLDER,
  MIME_FORM,
  MIME_PDF,
  MIME_SHEET,
  MIME_SLIDE,
} from "@/types/navigator";

// Lucide outlines tinted with the Google brand color for each
// Workspace type. Solid outlines read cleanly at 16px — detailed
// mini-scenes with gradients got muddy at that size.

// Renders the icon for a mime type as a JSX element. Returning an
// element (instead of a component reference) avoids the React Compiler
// "components created during render" lint rule — callers never have a
// PascalCase const that looks like a component definition.
export function renderMimeIcon(mimeType: string, className?: string): ReactElement {
  if (mimeType === MIME_FOLDER) {
    return <Folder className={className} style={{ color: "#5F6368" }} />;
  }
  if (mimeType === MIME_DOC) {
    return <FileText className={className} style={{ color: "#4285F4" }} />;
  }
  if (mimeType === MIME_SHEET) {
    return <Table className={className} style={{ color: "#0F9D58" }} />;
  }
  if (mimeType === MIME_SLIDE) {
    return <Presentation className={className} style={{ color: "#F4B400" }} />;
  }
  if (mimeType === MIME_DRAWING) {
    return <Palette className={className} style={{ color: "#EA4335" }} />;
  }
  if (mimeType === MIME_FORM) {
    return <ClipboardList className={className} style={{ color: "#673AB7" }} />;
  }
  if (mimeType === MIME_PDF) {
    return <FileType2 className={className} style={{ color: "#E53935" }} />;
  }
  if (mimeType.startsWith("image/")) {
    return <LucideImage className={className} style={{ color: "#00ACC1" }} />;
  }
  return <FileText className={className} style={{ color: "#78909C" }} />;
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
 * so the Navigator toolbar shouldn't duplicate it in the breadcrumb.
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
