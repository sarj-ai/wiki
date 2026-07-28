import "@/lib/api-client";

import type { z } from "zod";

import { API_URL } from "@/lib/env";
import { logoutAuthLogoutPost } from "@/lib/generated/sdk.gen";
import type { userProfile } from "@/lib/wiki-schemas";

export type User = z.output<typeof userProfile>;

export function getLoginUrl(): string {
  return `${API_URL}/auth/login`;
}

export async function logout(): Promise<void> {
  try {
    await logoutAuthLogoutPost({ throwOnError: true });
  } catch {
    return;
  }
}
