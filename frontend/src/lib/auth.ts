import { apiClient } from "@/lib/api-client";

const API_URL = process.env["NEXT_PUBLIC_API_URL"] ?? "http://localhost:8000";

export interface User {
  id: string;
  email: string;
  displayName: string | null;
  avatarUrl: string | null;
}

export function getLoginUrl(): string {
  return `${API_URL}/auth/login`;
}

export async function logout(): Promise<void> {
  try {
    await apiClient.post("auth/logout");
  } catch {
    // Logout is best-effort — the cookies clear server-side and the
    // caller drops local user state regardless.
  }
}
