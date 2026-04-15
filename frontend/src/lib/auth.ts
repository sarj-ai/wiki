const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export interface User {
  id: string;
  email: string;
  displayName: string | null;
  avatarUrl: string | null;
}

export function getLoginUrl(): string {
  return `${API_URL}/auth/login`;
}

export async function fetchCurrentUser(): Promise<User | null> {
  try {
    const res = await fetch(`${API_URL}/auth/me`, {
      credentials: "include",
    });
    if (!res.ok) return null;
    const data = await res.json();
    return {
      id: data.id,
      email: data.email,
      displayName: data.display_name,
      avatarUrl: data.avatar_url,
    };
  } catch {
    return null;
  }
}

export async function refreshAccessToken(): Promise<boolean> {
  try {
    const res = await fetch(`${API_URL}/auth/refresh`, {
      method: "POST",
      credentials: "include",
    });
    return res.ok;
  } catch {
    return false;
  }
}

export async function logout(): Promise<void> {
  await fetch(`${API_URL}/auth/logout`, {
    method: "POST",
    credentials: "include",
  });
}
