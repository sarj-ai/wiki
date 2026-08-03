import "server-only";

import { API_URL } from "@/lib/env";
import { zDocumentAliasRead } from "@/lib/generated/zod.gen";

export type AliasResolution =
  | { status: "found"; fileId: string }
  | { status: "not_found" }
  | { status: "unauthorized" }
  | { status: "unavailable" };

export async function resolveDocumentAlias(
  alias: string,
  accessToken: string,
): Promise<AliasResolution> {
  const upstream = await fetch(`${API_URL}/aliases/${encodeURIComponent(alias)}`, {
    cache: "no-store",
    headers: { cookie: `access_token=${accessToken}` },
    signal: AbortSignal.timeout(10_000),
  });
  if (!upstream.ok) {
    upstream.body?.cancel().catch(() => null);
    if (upstream.status === 401) return { status: "unauthorized" };
    if (upstream.status === 404) return { status: "not_found" };
    return { status: "unavailable" };
  }

  const parsed = zDocumentAliasRead.safeParse(await upstream.json());
  if (!parsed.success) return { status: "unavailable" };
  return { fileId: parsed.data.file_id, status: "found" };
}
