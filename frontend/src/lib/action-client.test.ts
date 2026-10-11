// @vitest-environment happy-dom

import { runAction } from "@/lib/action-client";
import { ApiError } from "@/lib/api-error";
import { claimBrowserSession, retireBrowserSession } from "@/lib/browser-session";
import { TERMINAL_UNAUTHORIZED_EVENT } from "@/lib/session-events";

describe("server action session expiry", () => {
  it("ignores an unsignalled action's late 401 after provider retirement", async () => {
    const previous = {};
    const replacement = {};
    claimBrowserSession(previous);
    const result = Promise.withResolvers<{ error: string; ok: false; status: number }>();
    const action = vi.fn(async () => await result.promise);
    const fetchMock = vi.fn<typeof fetch>();
    vi.stubGlobal("fetch", fetchMock);
    const listener = vi.fn<() => void>();
    window.addEventListener(TERMINAL_UNAUTHORIZED_EVENT, listener);
    try {
      const pending = runAction(action).catch((error: unknown) => error);
      retireBrowserSession(previous);
      claimBrowserSession(replacement);
      result.resolve({ error: "old user expired", ok: false, status: 401 });

      await expect(pending).resolves.toMatchObject({ name: "AbortError" });
      expect(action).toHaveBeenCalledOnce();
      expect(fetchMock).not.toHaveBeenCalled();
      expect(listener).not.toHaveBeenCalled();
    } finally {
      retireBrowserSession(replacement);
      window.removeEventListener(TERMINAL_UNAUTHORIZED_EVENT, listener);
      vi.unstubAllGlobals();
    }
  });

  it("drops a rejected late 401 without leaking it into an active provider's cache", async () => {
    const owner = {};
    claimBrowserSession(owner);
    const result = Promise.withResolvers<never>();
    const action = vi.fn(async () => await result.promise);
    const pending = runAction(action).catch((error: unknown) => error);
    retireBrowserSession(owner);
    result.reject(new ApiError(401, "Retired mutation expired"));

    await expect(pending).resolves.toMatchObject({ name: "AbortError" });
    expect(action).toHaveBeenCalledOnce();
  });

  it("does not replay an old action when its refresh finishes after retirement", async () => {
    const owner = {};
    claimBrowserSession(owner);
    const refresh = Promise.withResolvers<Response>();
    const fetchMock = vi.fn<typeof fetch>().mockReturnValue(refresh.promise);
    vi.stubGlobal("fetch", fetchMock);
    const action = vi.fn().mockResolvedValue({ error: "expired", ok: false, status: 401 });
    const pending = runAction(action).catch((error: unknown) => error);
    await vi.waitFor(() => {
      expect(fetchMock).toHaveBeenCalledOnce();
    });
    retireBrowserSession(owner);
    await expect(pending).resolves.toMatchObject({ name: "AbortError" });
    expect(fetchMock.mock.calls[0]?.[1]?.signal?.aborted).toBe(true);
    refresh.resolve(new Response(null));
    expect(action).toHaveBeenCalledOnce();
    vi.unstubAllGlobals();
  });

  it("lets an already-started mutation finish while dropping its retired caller's result", async () => {
    const owner = {};
    claimBrowserSession(owner);
    const result = Promise.withResolvers<{ data: string; ok: true }>();
    const completed = vi.fn<() => void>();
    const action = vi.fn(async () => {
      const response = await result.promise;
      completed();
      return response;
    });
    const pending = runAction(action).catch((error: unknown) => error);
    retireBrowserSession(owner);
    result.resolve({ data: "created", ok: true });
    await expect(pending).resolves.toMatchObject({ name: "AbortError" });
    expect(completed).toHaveBeenCalledOnce();
    expect(action).toHaveBeenCalledOnce();
  });

  it("discards a cancelled read waiting on the shared session refresh", async () => {
    const refresh = Promise.withResolvers<Response>();
    const fetchMock = vi.fn<typeof fetch>().mockReturnValue(refresh.promise);
    vi.stubGlobal("fetch", fetchMock);
    const expired = vi.fn().mockResolvedValue({ error: "expired", ok: false, status: 401 });
    const controller = new AbortController();
    const discarded = runAction(expired, controller.signal);
    const discardedResult = discarded.catch((error: unknown) => error);
    const retained = runAction(expired);
    await vi.waitFor(() => {
      expect(fetchMock).toHaveBeenCalledOnce();
    });

    controller.abort();
    await expect(discardedResult).resolves.toMatchObject({ name: "AbortError" });
    expired.mockResolvedValue({ data: "ready", ok: true });
    refresh.resolve(new Response(null));
    await expect(retained).resolves.toBe("ready");
    expect(expired).toHaveBeenCalledTimes(3);
    vi.unstubAllGlobals();
  });
  it("announces one terminal 401 after refresh cannot recover", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 401 })));
    const expired = vi.fn().mockResolvedValue({ error: "expired", ok: false, status: 401 });
    const listener = vi.fn();
    window.addEventListener(TERMINAL_UNAUTHORIZED_EVENT, listener);

    await expect(runAction(expired)).rejects.toMatchObject({ status: 401 });

    expect(listener).toHaveBeenCalledOnce();
    window.removeEventListener(TERMINAL_UNAUTHORIZED_EVENT, listener);
    vi.unstubAllGlobals();
  });

  it("keeps the session when refresh is temporarily unavailable", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 429 })));
    const expired = vi.fn().mockResolvedValue({ error: "expired", ok: false, status: 401 });
    const listener = vi.fn();
    window.addEventListener(TERMINAL_UNAUTHORIZED_EVENT, listener);

    await expect(runAction(expired)).rejects.toMatchObject({
      message: "Your session could not be refreshed. Try again in a moment.",
      status: 503,
    });

    expect(expired).toHaveBeenCalledOnce();
    expect(listener).not.toHaveBeenCalled();
    window.removeEventListener(TERMINAL_UNAUTHORIZED_EVENT, listener);
    vi.unstubAllGlobals();
  });

  it("replays the action only after a successful refresh", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 200 })));
    const action = vi
      .fn()
      .mockResolvedValueOnce({ error: "expired", ok: false, status: 401 })
      .mockResolvedValueOnce({ data: "ready", ok: true });

    await expect(runAction(action)).resolves.toBe("ready");
    expect(action).toHaveBeenCalledTimes(2);
    vi.unstubAllGlobals();
  });
});
