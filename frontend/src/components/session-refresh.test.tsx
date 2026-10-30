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
    expect(vi.getTimerCount()).toBe(0);
  });

  it("suspends hidden-tab checks and immediately refreshes on return after expiry", async () => {
    const visibility = vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
    const readMarker = vi.spyOn(localStorage, "getItem");
    flushSync(() => root.render(<SessionRefresh />));
    pending.resolve("refreshed");
    await vi.advanceTimersByTimeAsync(60 * 1000);
    visibility.mockReturnValue("hidden");
    document.dispatchEvent(new Event("visibilitychange"));
    const hiddenReads = readMarker.mock.calls.length;
    const hiddenChecks = visibility.mock.calls.length;
    expect(vi.getTimerCount()).toBe(0);
    await vi.advanceTimersByTimeAsync(30 * 60 * 1000);
    expect(readMarker).toHaveBeenCalledTimes(hiddenReads);
    expect(visibility).toHaveBeenCalledTimes(hiddenChecks);
    expect(deduplicatedRefresh).toHaveBeenCalledOnce();
    visibility.mockReturnValue("visible");
    document.dispatchEvent(new Event("visibilitychange"));
    expect(deduplicatedRefresh).toHaveBeenCalledTimes(2);
    expect(vi.getTimerCount()).toBe(1);
    document.dispatchEvent(new Event("visibilitychange"));
    expect(vi.getTimerCount()).toBe(1);
  });

  it.each(["getItem", "removeItem"] as const)(
    "still releases refresh ownership and all timers when storage %s throws during cleanup",
    async (operation) => {
      flushSync(() => root.render(<SessionRefresh />));
      const signal = vi.mocked(deduplicatedRefresh).mock.calls[0]?.[0];
      vi.spyOn(localStorage, operation).mockImplementation(() => {
        throw new DOMException("Storage denied", "SecurityError");
      });
      flushSync(() => root.render(null));
      expect(signal?.aborted).toBe(true);
      expect(vi.getTimerCount()).toBe(0);
      pending.resolve("unauthorized");
      await vi.advanceTimersByTimeAsync(0);
      expect(notifyTerminalUnauthorized).not.toHaveBeenCalled();
    },
  );

  it.each(["getItem", "setItem"] as const)(
    "refreshes with unavailable storage %s and retains the in-memory twenty-minute interval",
    async (operation) => {
      vi.spyOn(localStorage, operation).mockImplementation(() => {
        throw new DOMException("Storage denied", "SecurityError");
      });
      flushSync(() => root.render(<SessionRefresh />));
      expect(deduplicatedRefresh).toHaveBeenCalledOnce();
      pending.resolve("refreshed");
      await vi.advanceTimersByTimeAsync(19 * 60 * 1000);
      expect(deduplicatedRefresh).toHaveBeenCalledOnce();
      await vi.advanceTimersByTimeAsync(60 * 1000);
      expect(deduplicatedRefresh).toHaveBeenCalledTimes(2);
    },
  );

  it("observes marker removal by another owner instead of retaining an available-storage cache", async () => {
    flushSync(() => root.render(<SessionRefresh />));
    pending.resolve("refreshed");
    await vi.advanceTimersByTimeAsync(60 * 1000);
    expect(deduplicatedRefresh).toHaveBeenCalledOnce();
    localStorage.removeItem("sarj-wiki:last-session-refresh");
    await vi.advanceTimersByTimeAsync(60 * 1000);
    expect(deduplicatedRefresh).toHaveBeenCalledTimes(2);
  });

  it("retries an own failed refresh even when its marker cannot be removed", async () => {
    const remove = vi.spyOn(localStorage, "removeItem").mockImplementation(() => {
      throw new DOMException("Storage denied", "SecurityError");
    });
    flushSync(() => root.render(<SessionRefresh />));
    pending.resolve("unavailable");
    await vi.advanceTimersByTimeAsync(0);
    expect(localStorage.getItem("sarj-wiki:last-session-refresh")).not.toBeNull();
    expect(remove).toHaveBeenCalledOnce();
    vi.mocked(deduplicatedRefresh).mockResolvedValue("refreshed");
    await vi.advanceTimersByTimeAsync(60 * 1000);
    expect(deduplicatedRefresh).toHaveBeenCalledTimes(2);
  });

  it("does not ignore a replacement owner's marker after own failed-marker removal was denied", async () => {
    vi.spyOn(localStorage, "removeItem").mockImplementation(() => {
      throw new DOMException("Storage denied", "SecurityError");
    });
    flushSync(() => root.render(<SessionRefresh />));
    pending.resolve("unavailable");
    await vi.advanceTimersByTimeAsync(0);
    localStorage.setItem("sarj-wiki:last-session-refresh", String(Date.now() + 1000));
    await vi.advanceTimersByTimeAsync(60 * 1000);
    expect(deduplicatedRefresh).toHaveBeenCalledOnce();
  });

  it("does not leave a failed owned marker suppressing the Strict Mode replacement when removal is denied", async () => {
    vi.spyOn(localStorage, "removeItem").mockImplementation(() => {
      throw new DOMException("Storage denied", "SecurityError");
    });
    flushSync(() =>
      root.render(
        <StrictMode>
          <SessionRefresh />
        </StrictMode>,
      ),
    );
    expect(deduplicatedRefresh).toHaveBeenCalledTimes(2);
    expect(vi.mocked(deduplicatedRefresh).mock.calls.map(([signal]) => signal?.aborted)).toEqual([
      true,
      false,
    ]);
    pending.resolve("unavailable");
    await vi.advanceTimersByTimeAsync(0);
    vi.mocked(deduplicatedRefresh).mockResolvedValue("refreshed");
    await vi.advanceTimersByTimeAsync(60 * 1000);
    expect(deduplicatedRefresh).toHaveBeenCalledTimes(3);
  });

  it("preserves a replacement marker written during a denied owned-marker removal", () => {
    flushSync(() => root.render(<SessionRefresh />));
    vi.spyOn(localStorage, "removeItem").mockImplementation(() => {
      localStorage.setItem("sarj-wiki:last-session-refresh", "replacement-marker");
      throw new DOMException("Storage denied", "SecurityError");
    });
    flushSync(() => root.render(null));
    expect(localStorage.getItem("sarj-wiki:last-session-refresh")).toBe("replacement-marker");
    expect(vi.mocked(deduplicatedRefresh).mock.calls[0]?.[0]?.aborted).toBe(true);
  });
});
