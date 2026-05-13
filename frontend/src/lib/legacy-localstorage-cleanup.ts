/**
 * One-shot cleanup for orphaned localStorage keys left behind by the
 * W18 ``navigator-*`` → ``wiki-*`` rename. The code stopped reading or
 * writing the legacy keys at deploy time, but every existing browser
 * already had values stashed under the old names — localStorage
 * doesn't auto-evict, so those entries just hang around forever.
 *
 * Called once from ``QueryProvider`` on mount. ``removeItem`` is
 * idempotent (no-ops on missing keys), so the function is safe to run
 * on every load; we can drop the call entirely after a few weeks
 * once typical users have visited and the cleanup has propagated.
 */

const LEGACY_KEYS = [
  // Per-row sidebar state (use-expanded-folders.ts).
  "navigator-expanded-folders",
  "navigator-collapsed-drives",
  // Sidebar width persisted by resize-handle.tsx.
  "navigator-sidebar-width",
  // React Query persisted cache (query-provider.tsx). The new
  // ``sarj-wiki-query-cache`` is what the app reads/writes now.
  "sarj-navigator-query-cache",
];

export function cleanupLegacyLocalStorage(): void {
  if (typeof window === "undefined") return;
  for (const key of LEGACY_KEYS) {
    try {
      window.localStorage.removeItem(key);
    } catch {
      // Quota errors / private-mode storage failures are non-fatal —
      // the keys just stay until the user clears storage manually.
    }
  }
}
