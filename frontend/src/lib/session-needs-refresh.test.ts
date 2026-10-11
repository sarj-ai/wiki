import { sessionNeedsRefresh } from "@/lib/session-needs-refresh";

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("session expiry hint", () => {
  it("retains the existing thirty-second refresh window", () => {
    vi.useFakeTimers();
    vi.setSystemTime(1_000_000);
    vi.spyOn(document, "cookie", "get").mockReturnValue("session_exp=1030001");
    expect(sessionNeedsRefresh()).toBe(false);
    vi.advanceTimersByTime(2);
    expect(sessionNeedsRefresh()).toBe(true);
  });

  it.each(["", "not-a-time"])("does not refresh without a valid expiry hint: %s", (expiry) => {
    vi.spyOn(document, "cookie", "get").mockReturnValue(`session_exp=${expiry}`);
    expect(sessionNeedsRefresh()).toBe(false);
  });
});
