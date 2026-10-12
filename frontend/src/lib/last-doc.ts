import { recentDocsStorageKey } from "@/lib/browser-storage";
import { RecentDocsSchema } from "@/lib/storage-schemas";

const MAX_RECENT = 6;

export interface LastDoc {
  fileId: string;
  mimeType: string;
  title: string;
}

export function saveLastDoc(userId: string, doc: LastDoc): void {
  try {
    const key = recentDocsStorageKey(userId);
    const stored = readStoredRecentDocs(key);
    const raw = stored.ok ? stored.raw : null;
    const next = [doc, ...decodeRecentDocs(raw).filter((d) => d.fileId !== doc.fileId)].slice(
      0,
      MAX_RECENT,
    );
    const serialized = JSON.stringify(next);
    if (raw !== serialized) localStorage.setItem(key, serialized);
  } catch {
    // localStorage unavailable (private mode / quota) — warm-up just skips.
  }
}

export function readRecentDocs(userId: string): LastDoc[] {
  const stored = readStoredRecentDocs(recentDocsStorageKey(userId));
  return stored.ok ? decodeRecentDocs(stored.raw) : [];
}

type StoredRecentDocs = { ok: false } | { ok: true; raw: null | string };

function readStoredRecentDocs(key: string): StoredRecentDocs {
  try {
    return { ok: true, raw: localStorage.getItem(key) };
  } catch {
    return { ok: false };
  }
}

function decodeRecentDocs(raw: null | string): LastDoc[] {
  try {
    return RecentDocsSchema.parse(JSON.parse(raw ?? "[]"));
  } catch {
    return [];
  }
}
