"use client";

import { useSyncExternalStore } from "react";

const _noop = (): void => undefined;
const _subscribe = () => _noop;
const _getClient = () => true;
const _getServer = () => false;

export function useIsClient(): boolean {
  return useSyncExternalStore(_subscribe, _getClient, _getServer);
}
