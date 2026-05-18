"use client";

import { useSyncExternalStore } from "react";

/**
 * Returns ``true`` once the component has mounted on the client.
 *
 * Built on ``useSyncExternalStore`` so it reads ``false`` during SSR
 * and the very first render, then ``true`` after hydration — without
 * the set-state-in-effect pattern that React's purity rule rejects.
 * Use it to gate browser-only branches (theme-aware emoji picker,
 * post-hydration theme icon, etc.) so the server HTML and the
 * post-hydration DOM stay in sync.
 */
export function useIsClient(): boolean {
  return useSyncExternalStore(_subscribe, _getClient, _getServer);
}

const _noop = (): void => undefined;
const _subscribe = () => _noop;
const _getClient = () => true;
const _getServer = () => false;
