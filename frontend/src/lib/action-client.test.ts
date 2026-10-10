// @vitest-environment happy-dom

import { runAction } from "@/lib/action-client";
import { TERMINAL_UNAUTHORIZED_EVENT } from "@/lib/session-events";

describe("server action session expiry", () => {
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
