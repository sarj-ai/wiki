import {
  captureBrowserSession,
  claimBrowserSession,
  retireBrowserSession,
} from "@/lib/browser-session";
import { notifyTerminalUnauthorized, TERMINAL_UNAUTHORIZED_EVENT } from "@/lib/session-events";

it("adopts child work started before the provider claims the current session", () => {
  const startedByChild = captureBrowserSession();
  const owner = {};
  claimBrowserSession(owner);
  expect(captureBrowserSession()).toBe(startedByChild);
  expect(startedByChild.aborted).toBe(false);
  retireBrowserSession(owner);
  expect(startedByChild.aborted).toBe(true);
});

it("does not let stale cleanup retire the replacement provider's child work", () => {
  const previous = {};
  claimBrowserSession(previous);
  const oldSession = captureBrowserSession();
  retireBrowserSession(previous);
  const childSession = captureBrowserSession();
  const replacement = {};
  claimBrowserSession(replacement);
  retireBrowserSession(previous);

  expect(oldSession.aborted).toBe(true);
  expect(childSession.aborted).toBe(false);
  expect(captureBrowserSession()).toBe(childSession);
  retireBrowserSession(replacement);
});

it("allows standalone auth notifications after a provider retires while suppressing its old events", () => {
  const owner = {};
  claimBrowserSession(owner);
  const retiredSession = captureBrowserSession();
  retireBrowserSession(owner);
  const listener = vi.fn<() => void>();
  window.addEventListener(TERMINAL_UNAUTHORIZED_EVENT, listener);
  try {
    notifyTerminalUnauthorized(retiredSession);
    expect(listener).not.toHaveBeenCalled();
    notifyTerminalUnauthorized();
    expect(listener).toHaveBeenCalledOnce();
  } finally {
    window.removeEventListener(TERMINAL_UNAUTHORIZED_EVENT, listener);
  }
});
