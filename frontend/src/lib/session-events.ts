import { captureBrowserSession } from "@/lib/browser-session";

export const TERMINAL_UNAUTHORIZED_EVENT = "sarj-wiki:terminal-unauthorized";

export function notifyTerminalUnauthorized(session = captureBrowserSession()): void {
  if (typeof window === "undefined" || session.aborted) return;
  window.dispatchEvent(new Event(TERMINAL_UNAUTHORIZED_EVENT));
}
