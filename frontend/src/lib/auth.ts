import type { z } from "zod";

import { logoutAction } from "@/app/actions/wiki-actions";
import { runAction } from "@/lib/action-client";
import { API_URL } from "@/lib/env";
import type { userProfile } from "@/lib/wiki-schemas";

export type User = z.output<typeof userProfile>;

export function getLoginUrl(): string {
  return `${API_URL}/auth/login`;
}

export async function logout(): Promise<void> {
  try {
    await runAction(logoutAction);
  } catch {
    // Logout is best-effort because the local session is already invalid.
  }
}
