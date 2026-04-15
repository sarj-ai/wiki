import { MIME_DOC, MIME_DRAWING, MIME_FORM, MIME_SHEET, MIME_SLIDE } from "@/types/navigator";

// Builds the iframe src for a file. Google Workspace types use
// embedded=true variants of the product URL; everything else falls
// back to Drive's preview endpoint which handles PDFs, images, and
// unknown mime types gracefully.
export function embedUrlFor(mimeType: string, id: string): string {
  const encoded = encodeURIComponent(id);
  if (mimeType === MIME_DOC)
    return `https://docs.google.com/document/d/${encoded}/edit?embedded=true`;
  if (mimeType === MIME_SHEET)
    return `https://docs.google.com/spreadsheets/d/${encoded}/edit?embedded=true`;
  if (mimeType === MIME_SLIDE)
    return `https://docs.google.com/presentation/d/${encoded}/edit?embedded=true`;
  if (mimeType === MIME_DRAWING)
    return `https://docs.google.com/drawings/d/${encoded}/edit?embedded=true`;
  if (mimeType === MIME_FORM)
    return `https://docs.google.com/forms/d/${encoded}/edit?embedded=true`;
  return `https://drive.google.com/file/d/${encoded}/preview`;
}

// Non-embedded "open in Google" URL for the toolbar's click-through.
// PDFs and images land on Drive's `/view` endpoint since they have no
// dedicated Google editor.
export function openUrlFor(mimeType: string, id: string): string {
  const encoded = encodeURIComponent(id);
  if (mimeType === MIME_DOC) return `https://docs.google.com/document/d/${encoded}/edit`;
  if (mimeType === MIME_SHEET) return `https://docs.google.com/spreadsheets/d/${encoded}/edit`;
  if (mimeType === MIME_SLIDE) return `https://docs.google.com/presentation/d/${encoded}/edit`;
  if (mimeType === MIME_DRAWING) return `https://docs.google.com/drawings/d/${encoded}/edit`;
  if (mimeType === MIME_FORM) return `https://docs.google.com/forms/d/${encoded}/edit`;
  return `https://drive.google.com/file/d/${encoded}/view`;
}
