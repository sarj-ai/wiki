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
});
