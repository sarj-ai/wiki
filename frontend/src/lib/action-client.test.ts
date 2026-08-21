// @vitest-environment happy-dom

import { runAction } from "@/lib/action-client";
import { TERMINAL_UNAUTHORIZED_EVENT } from "@/lib/session-events";

describe("server action session expiry", () => {
  it("announces one terminal 401 after refresh cannot recover", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 401 })));
    const expired = vi.fn().mockResolvedValue({ error: "expired", ok: false, status: 401 });
    const listener = vi.fn();
    window.addEventListener(TERMINAL_UNAUTHORIZED_EVENT, listener);

    await expect(runAction(expired)).rejects.toMatchObject({ status: 401 });

    expect(listener).toHaveBeenCalledTimes(1);
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

    expect(expired).toHaveBeenCalledTimes(1);
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
