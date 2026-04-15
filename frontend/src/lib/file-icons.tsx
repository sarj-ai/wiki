import { FileIcon, Folder, Image as LucideImage } from "lucide-react";
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

// Brand-colored inline SVGs for Google Workspace types. Keeping them
// hand-rolled (rather than pulled from a material-symbols font or
// external CDN) means:
//   1. No network round-trip at first paint.
//   2. No CSP adjustment needed to allow a Google icon CDN.
//   3. They render at whatever size the Tailwind className dictates.
//
// Every icon is a 24×24 viewBox so size-4 (16px) and size-5 (20px)
// keep consistent proportions with the Lucide icons elsewhere in the
// app.

function DocPageShape({ fill, accent }: { fill: string; accent: string }) {
  // Shared "document with folded corner" background used by all five
  // Workspace product icons. Children render on top.
  return (
    <>
      <path d="M14 2H6C4.9 2 4 2.9 4 4v16c0 1.1.89 2 2 2h12c1.1 0 2-.9 2-2V8l-6-6z" fill={fill} />
      <path d="M14 2v6h6" fill={accent} />
    </>
  );
}

function GoogleDocIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden="true">
      <DocPageShape fill="#4285F4" accent="#A1C2FA" />
      <path d="M8 12h8M8 15h8M8 18h5" stroke="#fff" strokeWidth="1.2" strokeLinecap="round" />
    </svg>
  );
}

function GoogleSheetIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden="true">
      <DocPageShape fill="#0F9D58" accent="#87CEAC" />
      <path d="M8 11h8v8H8zM8 13.5h8M8 16.5h8M12 11v8" stroke="#fff" strokeWidth="1" fill="none" />
    </svg>
  );
}

function GoogleSlidesIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden="true">
      <DocPageShape fill="#F4B400" accent="#FCE8B2" />
      <rect
        x="7.5"
        y="11.5"
        width="9"
        height="6"
        rx="0.5"
        fill="none"
        stroke="#fff"
        strokeWidth="1"
      />
    </svg>
  );
}

function GoogleFormsIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden="true">
      <DocPageShape fill="#673AB7" accent="#B39DDB" />
      <path
        d="M8.2 12.8l1 1 2.2-2.3"
        stroke="#fff"
        strokeWidth="1.2"
        strokeLinecap="round"
        strokeLinejoin="round"
        fill="none"
      />
      <path d="M13 12.5h4" stroke="#fff" strokeWidth="1.2" strokeLinecap="round" />
      <path
        d="M8.2 16.3l1 1 2.2-2.3"
        stroke="#fff"
        strokeWidth="1.2"
        strokeLinecap="round"
        strokeLinejoin="round"
        fill="none"
      />
      <path d="M13 16h4" stroke="#fff" strokeWidth="1.2" strokeLinecap="round" />
    </svg>
  );
}

function GoogleDrawingIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden="true">
      <DocPageShape fill="#4285F4" accent="#A1C2FA" />
      <circle cx="9.5" cy="14" r="1.8" fill="#DB4437" />
      <rect x="12" y="15" width="3.5" height="3.5" fill="#0F9D58" />
    </svg>
  );
}

function PdfIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden="true">
      <DocPageShape fill="#D93025" accent="#F5B5B0" />
      <text
        x="12"
        y="18"
        textAnchor="middle"
        fontSize="6"
        fontWeight="700"
        fontFamily="system-ui, sans-serif"
        fill="#fff"
      >
        PDF
      </text>
    </svg>
  );
}

// Renders the icon for a mime type as a JSX element. Returning an
// element (instead of a component reference) avoids the React Compiler
// "components created during render" lint rule — callers never have a
// PascalCase const that looks like a component definition.
export function renderMimeIcon(mimeType: string, className?: string): ReactElement {
  if (mimeType === MIME_FOLDER) {
    // Folders keep the Lucide outline shape but get a soft brand color
    // so they stand out from the typed icons around them.
    return <Folder className={className} style={{ color: "#5F6368" }} />;
  }
  if (mimeType === MIME_DOC) return <GoogleDocIcon className={className} />;
  if (mimeType === MIME_SHEET) return <GoogleSheetIcon className={className} />;
  if (mimeType === MIME_SLIDE) return <GoogleSlidesIcon className={className} />;
  if (mimeType === MIME_DRAWING) return <GoogleDrawingIcon className={className} />;
  if (mimeType === MIME_FORM) return <GoogleFormsIcon className={className} />;
  if (mimeType === MIME_PDF) return <PdfIcon className={className} />;
  if (mimeType.startsWith("image/")) {
    return <LucideImage className={className} style={{ color: "#00ACC1" }} />;
  }
  return <FileIcon className={className} style={{ color: "#78909C" }} />;
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
