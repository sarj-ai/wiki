import { ZRecentDocs } from "@/lib/storage-schemas";

const RECENT_DOCS_KEY = "sarj-wiki:recent-docs";

const MAX_RECENT = 6;

export interface LastDoc {
  fileId: string;
  mimeType: string;
  title: string;
}

export function saveLastDoc(doc: LastDoc): void {
  try {
    const next = [doc, ...readRecentDocs().filter((d) => d.fileId !== doc.fileId)].slice(
      0,
      MAX_RECENT,
    );
    localStorage.setItem(RECENT_DOCS_KEY, JSON.stringify(next));
  } catch {
    // localStorage unavailable (private mode / quota) — warm-up just skips.
  }
}

export function readRecentDocs(): LastDoc[] {
  try {
    return ZRecentDocs.parse(JSON.parse(localStorage.getItem(RECENT_DOCS_KEY) ?? "[]"));
  } catch {
    return [];
  }
}
