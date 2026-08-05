import "server-only";

import { API_URL } from "@/lib/env";
import { zDocumentAliasRead, zRecentDocumentAlias } from "@/lib/generated/zod.gen";

export interface RecentDocumentAlias {
  alias: string;
  driveName: null | string;
  fileId: string;
  lastVisitedAt: string;
  mimeType: string;
  name: string;
}

export type RecentDocumentAliasesResult =
  | { aliases: RecentDocumentAlias[]; status: "found" }
  | { status: "unauthorized" }
  | { status: "unavailable" };

export type AliasResolution =
  | { status: "found"; fileId: string }
  | { status: "not_found" }
  | { status: "unauthorized" }
  | { status: "unavailable" };

export async function resolveDocumentAlias(
  alias: string,
  accessToken: string,
): Promise<AliasResolution> {
  const upstream = await fetch(`${API_URL}/aliases/visits/${encodeURIComponent(alias)}`, {
    cache: "no-store",
    headers: { cookie: `access_token=${accessToken}` },
    method: "POST",
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

export async function getRecentDocumentAliases(
  accessToken: string,
): Promise<RecentDocumentAliasesResult> {
  const upstream = await fetch(`${API_URL}/aliases/recent`, {
    cache: "no-store",
    headers: { cookie: `access_token=${accessToken}` },
    signal: AbortSignal.timeout(10_000),
  });
  if (!upstream.ok) {
    upstream.body?.cancel().catch(() => null);
    if (upstream.status === 401) return { status: "unauthorized" };
    return { status: "unavailable" };
  }

  const parsed = zRecentDocumentAlias.array().safeParse(await upstream.json());
  if (!parsed.success) return { status: "unavailable" };
  return {
    aliases: parsed.data.map((item) => ({
      alias: item.alias,
      driveName: item.drive_name,
      fileId: item.file_id,
      lastVisitedAt: item.last_visited_at,
      mimeType: item.mime_type,
      name: item.name,
    })),
    status: "found",
  };
}
