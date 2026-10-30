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
  removeLocalStorageKeys([
    recentDocsStorageKey(userId),
    recentAliasesStorageKey(userId),
    expandedFoldersStorageKey(userId),
    expandedDrivesStorageKey(userId),
    collapsedDrivesStorageKey(userId),
    LAST_REFRESH_KEY,
  ]);
  removeSessionStorageKeys([ACTIVE_USER_KEY, REDIRECT_KEY, markdownRecoveryStorageKey(userId)]);
}

export function purgeLegacyBrowserStorage(): void {
  removeLocalStorageKeys(LEGACY_KEYS);
}

export function readActiveBrowserUser(): null | string {
  const result = readSessionStorage(ACTIVE_USER_KEY);
  return result.ok ? result.value : null;
}

export function writeActiveBrowserUser(userId: string): void {
  try {
    window.sessionStorage.setItem(ACTIVE_USER_KEY, userId);
  } catch {
    // Session isolation remains best-effort when browser storage is unavailable.
  }
}

export function storeLoginRedirect(path: string): void {
  try {
    window.sessionStorage.setItem(REDIRECT_KEY, path);
  } catch {
    // The login URL query parameter remains available as a fallback.
  }
}

export function takeLoginRedirect(): null | string {
  const result = readSessionStorage(REDIRECT_KEY);
  try {
    window.sessionStorage.removeItem(REDIRECT_KEY);
  } catch {
    // A failed removal cannot expose data to a different session namespace.
  }
  return result.ok ? result.value : null;
}

type StorageRead = { ok: false } | { ok: true; value: null | string };

function readSessionStorage(key: string): StorageRead {
  try {
    return { ok: true, value: window.sessionStorage.getItem(key) };
  } catch {
    return { ok: false };
  }
}

function removeLocalStorageKeys(keys: readonly string[]): void {
  for (const key of keys) {
    try {
      window.localStorage.removeItem(key);
    } catch {
      // Keep clearing independent keys when one browser-storage operation fails.
    }
  }
}

function removeSessionStorageKeys(keys: readonly string[]): void {
  for (const key of keys) {
    try {
      window.sessionStorage.removeItem(key);
    } catch {
      // Keep clearing independent keys when one browser-storage operation fails.
    }
  }
}
