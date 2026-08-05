import { ZRecentAliasVisits } from "@/lib/storage-schemas";

const RECENT_ALIAS_KEY_PREFIX = "sarj-wiki:recent-go";
export const MAX_RECENT_ALIASES = 50;

export interface RecentAliasVisit {
  alias: string;
  visitedAt: string;
}

export function recordRecentAlias(userId: string, alias: string): void {
  const visitedAt = new Date().toISOString();
  const next = [
    { alias, visitedAt },
    ...readRecentAliases(userId).filter((visit) => visit.alias !== alias),
  ];
  writeRecentAliases(userId, next);
}

export function readRecentAliases(userId: string): RecentAliasVisit[] {
  try {
    const raw = window.localStorage.getItem(storageKey(userId)) ?? "[]";
    return ZRecentAliasVisits.parse(JSON.parse(raw));
  } catch {
    return [];
  }
}

export function writeRecentAliases(userId: string, visits: RecentAliasVisit[]): void {
  try {
    const capped = ZRecentAliasVisits.parse(visits.slice(0, MAX_RECENT_ALIASES));
    window.localStorage.setItem(storageKey(userId), JSON.stringify(capped));
  } catch {
    // Browser storage can be unavailable or full; quick links remain optional.
  }
}

function storageKey(userId: string): string {
  return `${RECENT_ALIAS_KEY_PREFIX}:${userId}`;
}
