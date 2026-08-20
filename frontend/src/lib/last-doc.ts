import { recentDocsStorageKey } from "@/lib/browser-storage";
import { ZRecentDocs } from "@/lib/storage-schemas";

const MAX_RECENT = 6;

export interface LastDoc {
  fileId: string;
  mimeType: string;
  title: string;
}

export function saveLastDoc(userId: string, doc: LastDoc): void {
  try {
    const next = [doc, ...readRecentDocs(userId).filter((d) => d.fileId !== doc.fileId)].slice(
      0,
      MAX_RECENT,
    );
    localStorage.setItem(recentDocsStorageKey(userId), JSON.stringify(next));
  } catch {
    // localStorage unavailable (private mode / quota) — warm-up just skips.
  }
}

export function readRecentDocs(userId: string): LastDoc[] {
  const result = parseRecentDocs(userId);
  return result.ok ? result.value : [];
}

type RecentDocsResult = { ok: false } | { ok: true; value: LastDoc[] };

function parseRecentDocs(userId: string): RecentDocsResult {
  try {
    const value = ZRecentDocs.parse(
      JSON.parse(localStorage.getItem(recentDocsStorageKey(userId)) ?? "[]"),
    );
    return { ok: true, value };
  } catch {
    return { ok: false };
  }
}
