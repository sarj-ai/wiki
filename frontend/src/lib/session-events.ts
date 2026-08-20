export const TERMINAL_UNAUTHORIZED_EVENT = "sarj-wiki:terminal-unauthorized";

export function notifyTerminalUnauthorized(): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new Event(TERMINAL_UNAUTHORIZED_EVENT));
}
