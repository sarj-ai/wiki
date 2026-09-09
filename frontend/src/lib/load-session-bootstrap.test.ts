import { loadSessionBootstrap } from "@/lib/load-session-bootstrap";
import type { Bootstrap, UserProfile } from "@/lib/wiki-api";

const ME: UserProfile = {
  id: "authenticated-user",
  email: "qa@sarj.ai",
  displayName: "QA",
  avatarUrl: undefined,
};

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
});
