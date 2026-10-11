import { StrictMode } from "react";
import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";

import { deduplicatedRefresh, type SessionRefreshResult } from "@/lib/session-client";
import { notifyTerminalUnauthorized } from "@/lib/session-events";
import { installLocalStorage } from "@/test-stubs/install-local-storage";

import { SessionRefresh } from "./session-refresh";

vi.mock("@/lib/session-client", () => ({ deduplicatedRefresh: vi.fn() }));
vi.mock("@/lib/session-events", () => ({ notifyTerminalUnauthorized: vi.fn() }));

describe("background session refresh ownership", () => {
  let root: Root;
  let pending: ReturnType<typeof Promise.withResolvers<SessionRefreshResult>>;

  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-10-10T12:00:00Z"));
    installLocalStorage();
    vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
    pending = Promise.withResolvers<SessionRefreshResult>();
    vi.mocked(deduplicatedRefresh).mockReset().mockReturnValue(pending.promise);
    vi.mocked(notifyTerminalUnauthorized).mockReset();
    root = createRoot(document.createElement("div"));
  });

  afterEach(() => {
    flushSync(() => root.unmount());
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("does not clear or redirect the replacement session after a late denied refresh", async () => {
    flushSync(() => root.render(<SessionRefresh />));
    const signal = vi.mocked(deduplicatedRefresh).mock.calls[0]?.[0];
    expect(signal?.aborted).toBe(false);
    flushSync(() => root.render(null));
    localStorage.setItem("sarj-wiki:last-session-refresh", "replacement-marker");
    expect(signal?.aborted).toBe(true);
    pending.resolve("unauthorized");
    await vi.advanceTimersByTimeAsync(0);
    expect(notifyTerminalUnauthorized).not.toHaveBeenCalled();
    expect(localStorage.getItem("sarj-wiki:last-session-refresh")).toBe("replacement-marker");
  });

  it("releases an abandoned marker so Strict Mode remounts can observe failure and retry", async () => {
    flushSync(() =>
      root.render(
        <StrictMode>
          <SessionRefresh />
        </StrictMode>,
      ),
    );
    expect(deduplicatedRefresh).toHaveBeenCalledTimes(2);
    expect(vi.mocked(deduplicatedRefresh).mock.calls[0]?.[0]?.aborted).toBe(true);
    pending.resolve("unavailable");
    await vi.advanceTimersByTimeAsync(0);
    expect(localStorage.getItem("sarj-wiki:last-session-refresh")).toBeNull();
    vi.mocked(deduplicatedRefresh).mockResolvedValue("refreshed");
    await vi.advanceTimersByTimeAsync(60 * 1_000);
    expect(deduplicatedRefresh).toHaveBeenCalledTimes(3);
    expect(localStorage.getItem("sarj-wiki:last-session-refresh")).not.toBeNull();
  });

  it("preserves a later owner's marker during unmount", () => {
    flushSync(() => root.render(<SessionRefresh />));
    localStorage.setItem("sarj-wiki:last-session-refresh", "replacement-marker");
    flushSync(() => root.render(null));
    expect(localStorage.getItem("sarj-wiki:last-session-refresh")).toBe("replacement-marker");
  });

  it("keeps terminal unauthorized handling for the current owner", async () => {
    flushSync(() => root.render(<SessionRefresh />));
    pending.resolve("unauthorized");
    await vi.advanceTimersByTimeAsync(0);
    expect(notifyTerminalUnauthorized).toHaveBeenCalledOnce();
    expect(localStorage.getItem("sarj-wiki:last-session-refresh")).toBeNull();
  });

  it("avoids background refresh requests in a hidden tab", async () => {
    vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
    flushSync(() => root.render(<SessionRefresh />));
    await vi.advanceTimersByTimeAsync(20 * 60 * 1_000);
    expect(deduplicatedRefresh).not.toHaveBeenCalled();
  });
});
