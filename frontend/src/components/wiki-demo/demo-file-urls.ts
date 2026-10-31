import { demoAssetUrl, type DemoFile } from "./demo-data";

const GOOGLE_PREVIEW =
  /^https:\/\/(?:docs\.google\.com\/(?:document|spreadsheets|presentation)|drive\.google\.com\/file)\/d\/[A-Za-z0-9_-]{1,128}\/preview$/u;
const GOOGLE_EXPORT =
  /^https:\/\/docs\.google\.com\/(?:document\/d\/[A-Za-z0-9_-]{1,128}\/export\?format=docx|spreadsheets\/d\/[A-Za-z0-9_-]{1,128}\/export\?format=xlsx|presentation\/d\/[A-Za-z0-9_-]{1,128}\/export\/pptx)$/u;
const DRIVE_DOWNLOAD =
  /^https:\/\/drive\.google\.com\/uc\?(?:export=download&id=[A-Za-z0-9_-]{1,128}|id=[A-Za-z0-9_-]{1,128}&export=download)$/u;

export function demoPreviewUrl(file: DemoFile): null | string {
  return file.previewUrl !== undefined && GOOGLE_PREVIEW.test(file.previewUrl)
    ? file.previewUrl
    : null;
}

export function demoDownloadUrl(file: DemoFile): null | string {
  if (file.downloadUrl === undefined) return demoAssetUrl(file.downloadFilename);
  return GOOGLE_EXPORT.test(file.downloadUrl) || DRIVE_DOWNLOAD.test(file.downloadUrl)
    ? file.downloadUrl
    : null;
}
