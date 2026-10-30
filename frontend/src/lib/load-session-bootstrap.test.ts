import { ApiError } from "@/lib/api-error";
import { loadSessionBootstrap } from "@/lib/load-session-bootstrap";
import type { Bootstrap, UserProfile } from "@/lib/wiki-api";

const ME: UserProfile = {
  id: "authenticated-user",
  email: "qa@sarj.ai",
  displayName: "QA",
  avatarUrl: undefined,
};

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("server session bootstrap recovery", () => {
  it("keeps the verified bootstrap identity without another authentication request", async () => {
    const bootstrap: Bootstrap = {
      me: ME,
      docIcons: [],
      drives: [],
      firstDriveTree: undefined,
      folderIcons: [],
      preferences: { hiddenWorkspaceIds: [], showNonDocFiles: false },
      sortOrders: [],
    };
    const api = {
      getBootstrap: vi.fn().mockResolvedValue(bootstrap),
      getMe: vi.fn<() => Promise<UserProfile>>(),
    };
    await expect(loadSessionBootstrap(api)).resolves.toEqual({ bootstrap, me: ME });
    expect(api.getMe).not.toHaveBeenCalled();
  });

  it("recovers identity with a fresh authenticated read when Drive preload fails", async () => {
    const api = {
      getBootstrap: vi
        .fn<() => Promise<Bootstrap>>()
        .mockRejectedValue(new Error("Drive unavailable")),
      getMe: vi.fn().mockResolvedValue(ME),
    };
    await expect(loadSessionBootstrap(api)).resolves.toEqual({ bootstrap: null, me: ME });
    expect(api.getMe).toHaveBeenCalledOnce();
  });

  it("remains anonymous when fresh authentication also fails", async () => {
    const api = {
      getBootstrap: vi.fn<() => Promise<Bootstrap>>().mockRejectedValue(new Error("Unauthorized")),
      getMe: vi.fn<() => Promise<UserProfile>>().mockRejectedValue(new Error("Unauthorized")),
    };
    await expect(loadSessionBootstrap(api)).resolves.toEqual({ bootstrap: null, me: null });
  });

  it("starts fresh identity recovery after the provider preload deadline", async () => {
    mockAbortTimeout();
    const api = {
      getBootstrap: vi.fn(waitUntilAbort),
      getMe: vi.fn().mockResolvedValue(ME),
    };
    const pending = loadSessionBootstrap(api);

    await vi.advanceTimersByTimeAsync(1_499);
    expect(api.getMe).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);

    await expect(pending).resolves.toEqual({ bootstrap: null, me: ME });
    expect(api.getMe).toHaveBeenCalledOnce();
  });

  it("bounds both stalled reads to a total 3 seconds without trusting cached identity", async () => {
    mockAbortTimeout();
    const api = {
      getBootstrap: vi.fn(waitUntilAbort),
      getMe: vi.fn(waitUntilAbort),
    };
    const pending = loadSessionBootstrap(api);
    const startedAt = Date.now();

    await vi.advanceTimersByTimeAsync(3_000);

    await expect(pending).resolves.toEqual({ bootstrap: null, me: null });
    expect(Date.now() - startedAt).toBe(3_000);
  });

  it("still verifies app identity when provider authentication returns 401", async () => {
    const api = {
      getBootstrap: vi
        .fn<() => Promise<Bootstrap>>()
        .mockRejectedValue(new ApiError(401, "Google login required")),
      getMe: vi.fn().mockResolvedValue(ME),
    };

    await expect(loadSessionBootstrap(api)).resolves.toEqual({ bootstrap: null, me: ME });
    expect(api.getMe).toHaveBeenCalledOnce();
  });
});

function mockAbortTimeout(): void {
  vi.useFakeTimers();
  vi.spyOn(AbortSignal, "timeout").mockImplementation((milliseconds) => {
    const controller = new AbortController();
    setTimeout(() => {
      controller.abort(new DOMException("The operation timed out", "TimeoutError"));
    }, milliseconds);
    return controller.signal;
  });
}

async function waitUntilAbort(signal?: AbortSignal): Promise<never> {
  if (!signal) throw new Error("A bootstrap deadline is required");
  return await new Promise<never>((_resolve, reject) => {
    signal.addEventListener(
      "abort",
      () => reject(new DOMException("The operation was aborted", "AbortError")),
      {
        once: true,
      },
    );
  });
}
