import { ZLastDoc } from "@/lib/storage-schemas";

const LAST_DOC_KEY = "sarj-wiki:last-doc";

export interface LastDoc {
  fileId: string;
  mimeType: string;
  title: string;
}

export function saveLastDoc(doc: LastDoc): void {
  try {
    localStorage.setItem(LAST_DOC_KEY, JSON.stringify(doc));
  } catch {
    // localStorage unavailable (private mode / quota) — warm-up just skips.
  }
}

export function readLastDoc(): LastDoc | null {
  try {
    return ZLastDoc.parse(JSON.parse(localStorage.getItem(LAST_DOC_KEY) ?? "null"));
  } catch {
    return null;
  }
}
