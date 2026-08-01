import { MIME_DOC, MIME_DRAWING, MIME_FORM, MIME_SHEET, MIME_SLIDE } from "@/types/wiki";

const EDITOR_PATHS: Record<string, string> = {
  [MIME_DOC]: "document",
  [MIME_DRAWING]: "drawings",
  [MIME_FORM]: "forms",
  [MIME_SHEET]: "spreadsheets",
  [MIME_SLIDE]: "presentation",
};

export function embedUrlFor(mimeType: string, id: string): string {
  return workspaceUrl(mimeType, id, "preview");
}

export function openUrlFor(mimeType: string, id: string): string {
  return workspaceUrl(mimeType, id, "view");
}

export function folderUrlFor(id: string): string {
  return `https://drive.google.com/drive/folders/${encodeURIComponent(id)}`;
}

function workspaceUrl(mimeType: string, id: string, driveFallback: "preview" | "view"): string {
  const encoded = encodeURIComponent(id);
  const path = EDITOR_PATHS[mimeType];
  return path
    ? `https://docs.google.com/${path}/d/${encoded}/edit`
    : `https://drive.google.com/file/d/${encoded}/${driveFallback}`;
}
