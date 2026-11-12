import {
  MIME_DOC,
  MIME_DRAWING,
  MIME_FORM,
  MIME_SHEET,
  MIME_SLIDE,
} from "@/types/wiki";

const EDITOR_PATHS: Record<string, string> = {
  [MIME_DOC]: "document",
  [MIME_DRAWING]: "drawings",
  [MIME_FORM]: "forms",
  [MIME_SHEET]: "spreadsheets",
  [MIME_SLIDE]: "presentation",
};

export type DocumentMode = "edit" | "preview";

export function supportsDocumentMode(mimeType: string): boolean {
  return (
    mimeType === MIME_DOC || mimeType === MIME_SHEET || mimeType === MIME_SLIDE
  );
}

export function embedUrlFor(
  mimeType: string,
  id: string,
  mode: DocumentMode = "edit",
): string {
  return workspaceUrl(
    mimeType,
    id,
    "preview",
    supportsDocumentMode(mimeType) ? mode : "edit",
  );
}

export function openUrlFor(mimeType: string, id: string): string {
  return workspaceUrl(mimeType, id, "view");
}

export function mobileReadUrlFor(mimeType: string, id: string): null | string {
  return mimeType === MIME_DOC
    ? workspaceUrl(mimeType, id, "view", "mobilebasic")
    : null;
}

export function folderUrlFor(id: string): string {
  return `https://drive.google.com/drive/folders/${encodeURIComponent(id)}`;
}

function workspaceUrl(
  mimeType: string,
  id: string,
  driveFallback: "preview" | "view",
  editorMode: DocumentMode | "mobilebasic" = "edit",
): string {
  const encoded = encodeURIComponent(id);
  const path = EDITOR_PATHS[mimeType];
  return path
    ? `https://docs.google.com/${path}/d/${encoded}/${editorMode}`
    : `https://drive.google.com/file/d/${encoded}/${driveFallback}`;
}
