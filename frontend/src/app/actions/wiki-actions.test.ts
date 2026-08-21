import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ driveSearch: vi.fn() }));

vi.mock("@/lib/server-api", () => {
  class ServerApiError extends Error {
    readonly status: number;

    constructor(status: number) {
      super(`Server API error: ${status}`);
      this.status = status;
    }
  }
  return {
    ServerApiError,
    serverWikiApi: { driveSearch: mocks.driveSearch },
  };
});

import { ServerApiError } from "@/lib/server-api";

import { searchDriveAction } from "./wiki-actions";

describe("Wiki Server Action errors", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("uses stable copy for a perimeter rate limit", async () => {
    mocks.driveSearch.mockRejectedValue(new ServerApiError(429));

    await expect(searchDriveAction("roadmap")).resolves.toEqual({
      error: "Too many requests. Try again in a minute.",
      ok: false,
      status: 429,
    });
  });
});
