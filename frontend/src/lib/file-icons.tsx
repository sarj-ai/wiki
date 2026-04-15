import {
  FileIcon,
  FileImage,
  FileText,
  FileType2,
  Folder,
  NotebookText,
  PencilRuler,
  Presentation,
  SquareCheckBig,
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

// Renders the icon for a mime type as a JSX element. Returning an
// element (instead of a component reference) avoids the React Compiler
// "components created during render" lint rule — callers never have a
// PascalCase const that looks like a component definition.
export function renderMimeIcon(mimeType: string, className?: string): ReactElement {
  if (mimeType === MIME_FOLDER) return <Folder className={className} />;
  if (mimeType === MIME_DOC) return <NotebookText className={className} />;
  if (mimeType === MIME_SHEET) return <Table className={className} />;
  if (mimeType === MIME_SLIDE) return <Presentation className={className} />;
  if (mimeType === MIME_DRAWING) return <PencilRuler className={className} />;
  if (mimeType === MIME_FORM) return <SquareCheckBig className={className} />;
  if (mimeType === MIME_PDF) return <FileType2 className={className} />;
  if (mimeType.startsWith("image/")) return <FileImage className={className} />;
  if (mimeType.startsWith("text/")) return <FileText className={className} />;
  return <FileIcon className={className} />;
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
