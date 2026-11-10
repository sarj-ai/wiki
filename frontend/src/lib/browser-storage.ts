import { markdownRecoveryStorageKey } from "@/lib/markdown-transform-recovery";

const LEGACY_KEYS = [
  "sarj-wiki-query-cache",
  "sarj-wiki:recent-docs",
  "wiki-collapsed-drives",
  "wiki-expanded-folders",
] as const;

const ACTIVE_USER_KEY = "sarj-wiki:active-user";
const LAST_REFRESH_KEY = "sarj-wiki:last-session-refresh";
const RECENT_ALIAS_KEY_PREFIX = "sarj-wiki:recent-go";
const REDIRECT_KEY = "sarj-redirect";

export function recentDocsStorageKey(userId: string): string {
  return `sarj-wiki:recent-docs:${userId}`;
}

export function recentAliasesStorageKey(userId: string): string {
  return `${RECENT_ALIAS_KEY_PREFIX}:${userId}`;
}

export function expandedFoldersStorageKey(userId: string): string {
  return `sarj-wiki:expanded-folders:${userId}`;
}

export function expandedDrivesStorageKey(userId: string): string {
  return `sarj-wiki:expanded-drives:${userId}`;
}

export function collapsedDrivesStorageKey(userId: string): string {
  return `sarj-wiki:collapsed-drives:${userId}`;
}

export function clearUserBrowserStorage(userId: string): void {
  removeStorageKeys("localStorage", [
    recentDocsStorageKey(userId),
    recentAliasesStorageKey(userId),
    expandedFoldersStorageKey(userId),
    expandedDrivesStorageKey(userId),
    collapsedDrivesStorageKey(userId),
    LAST_REFRESH_KEY,
  ]);
  removeStorageKeys("sessionStorage", [
    ACTIVE_USER_KEY,
    REDIRECT_KEY,
    markdownRecoveryStorageKey(userId),
  ]);
}

export function purgeLegacyBrowserStorage(): void {
  removeStorageKeys("localStorage", LEGACY_KEYS);
}

export function readActiveBrowserUser(): null | string {
  return readSessionStorage(ACTIVE_USER_KEY);
}

export function writeActiveBrowserUser(userId: string): void {
  writeSessionStorage(ACTIVE_USER_KEY, userId);
}

export function storeLoginRedirect(path: string): void {
  writeSessionStorage(REDIRECT_KEY, path);
}

export function takeLoginRedirect(): null | string {
  const value = readSessionStorage(REDIRECT_KEY);
  removeStorageKeys("sessionStorage", [REDIRECT_KEY]);
  return value;
}

function readSessionStorage(key: string): null | string {
  try {
    return window.sessionStorage.getItem(key);
  } catch {
    console.warn("Wiki browser storage is unavailable");
    return null;
  }
}

function writeSessionStorage(key: string, value: string): void {
  try {
    window.sessionStorage.setItem(key, value);
  } catch {
    // Browser metadata is best-effort when storage is unavailable.
  }
}

function removeStorageKeys(
  area: "localStorage" | "sessionStorage",
  keys: readonly string[],
): void {
  for (const key of keys) {
    try {
      window[area].removeItem(key);
    } catch {
      // Keep clearing independent keys when one browser-storage operation fails.
    }
  }
}
