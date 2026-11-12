import {
  type DocumentMode,
  embedUrlFor,
  supportsDocumentMode,
} from "@/lib/file-urls";

import { DEMO_FILES, type DemoFile, demoHref } from "./demo-data";

const GOOGLE_PREVIEW =
  /^https:\/\/(?:docs\.google\.com\/(?:document|spreadsheets|presentation)|drive\.google\.com\/file)\/d\/[A-Za-z0-9_-]{1,128}\/preview$/u;
const SHEET_TAB_PREVIEW =
  /^https:\/\/docs\.google\.com\/spreadsheets\/d\/[A-Za-z0-9_-]{1,128}\/preview#gid=[0-9]{1,10}$/u;
const GOOGLE_EXPORT =
  /^https:\/\/docs\.google\.com\/(?:document\/d\/[A-Za-z0-9_-]{1,128}\/export\?format=docx(?:&tab=t\.0)?|spreadsheets\/d\/[A-Za-z0-9_-]{1,128}\/export\?format=xlsx|presentation\/d\/[A-Za-z0-9_-]{1,128}\/export\/pptx)$/u;
const DRIVE_DOWNLOAD =
  /^https:\/\/drive\.google\.com\/uc\?(?:export=download&id=[A-Za-z0-9_-]{1,128}|id=[A-Za-z0-9_-]{1,128}&export=download)$/u;
const DRIVE_VIEW =
  /^https:\/\/drive\.google\.com\/file\/d\/[A-Za-z0-9_-]{1,128}\/view$/u;
const PUBLIC_FILE_ALIASES: ReadonlyMap<string, string> = new Map([
  ["1VzPtXow0E-cGFSTrQCQymMer-Fa5eYVDNf_jlH44rJA", "latency-guide"],
  ["12M9C0VB90yj2zoaowlshi7papBMfoFhJlroYr4lTDKQ", "ai-draft"],
  ["1BDbrvpKtmGG57TzGNnCxIChxztmfssUwJNxA44SwSVI", "latency-workbook"],
  ["1JEdWZnuH9oIZ3JYv95k6a516hE2w5fzv9fiGhEtGjWY", "latency-slides"],
  ["1EJbR3gurhRRvIB1RFhVoKxiBtyLHHqu4WcATUSa3M74", "latency-guide"],
  ["1ABWhf8XI2pxgC_uvAjkhSTapxAwD10iRgtpEJHSic0w", "latency-slides"],
]);

export function demoPreviewUrl(file: DemoFile): null | string {
  return GOOGLE_PREVIEW.test(file.previewUrl) ||
    SHEET_TAB_PREVIEW.test(file.previewUrl)
    ? file.previewUrl
    : null;
}

export function demoEmbedUrl(
  file: DemoFile,
  mode: DocumentMode,
): null | string {
  const preview = demoPreviewUrl(file);
  if (preview === null || !supportsDocumentMode(file.mimeType)) return preview;
  const url = new URL(preview);
  const id = url.pathname.split("/", 4)[3];
  if (
    id === undefined ||
    `${url.origin}${url.pathname}` !== embedUrlFor(file.mimeType, id, "preview")
  )
    return null;
  return `${embedUrlFor(file.mimeType, id, mode)}${url.hash}`;
}

export function demoHrefForDocumentPath(path: string): null | string {
  const id = /^\/d\/([A-Za-z0-9_-]{1,128})$/u.exec(path)?.[1];
  if (id === undefined) return null;
  const aliasedDemoId = PUBLIC_FILE_ALIASES.get(id);
  const file = DEMO_FILES.find((candidate) => {
    const preview = demoPreviewUrl(candidate);
    return (
      preview !== null &&
      (new URL(preview).pathname.split("/", 4)[3] === id ||
        aliasedDemoId === candidate.id)
    );
  });
  return file === undefined ? null : demoHref(file);
}

export function demoDownloadUrl(file: DemoFile): null | string {
  return GOOGLE_EXPORT.test(file.downloadUrl) ||
    DRIVE_DOWNLOAD.test(file.downloadUrl)
    ? file.downloadUrl
    : null;
}

export function demoViewUrl(file: DemoFile): null | string {
  return GOOGLE_PREVIEW.test(file.viewUrl) ||
    SHEET_TAB_PREVIEW.test(file.viewUrl) ||
    DRIVE_VIEW.test(file.viewUrl)
    ? file.viewUrl
    : null;
}
