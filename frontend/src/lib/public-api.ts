import "server-only";

import { makeCall } from "@/lib/api-client";
import { API_URL } from "@/lib/env";
import { createClient, createConfig } from "@/lib/generated/client";
import { getLinkMetaLinkMetaFileIdGet } from "@/lib/generated/sdk.gen";
import { zLinkMeta } from "@/lib/generated/zod.gen";

// Unauthenticated client: /link-meta is public by design, serving og:title
// to link-preview crawlers that carry no session cookie.
const publicClient = createClient(createConfig({ baseUrl: API_URL, cache: "no-store" }));

const call = makeCall(publicClient);

export async function getPublicDocName(fileId: string): Promise<string | null> {
  try {
    const meta = await call(
      getLinkMetaLinkMetaFileIdGet,
      // Timeboxed so a slow backend can't stall page render for crawlers —
      // link metadata is best-effort in latency, not just in errors.
      { path: { file_id: fileId }, signal: AbortSignal.timeout(3000) },
      zLinkMeta,
    );
    return meta.name ?? null;
  } catch {
    return null;
  }
}
