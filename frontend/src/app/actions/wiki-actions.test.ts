import { beforeEach, describe, expect, it, vi } from "vitest";

const MOCKS = vi.hoisted(() => ({ renameFile: vi.fn() }));

vi.mock("@/lib/server-api", () => {
  class ServerApiError extends Error {
    readonly status: number;

    constructor(status: number, options: ErrorOptions) {
      super(`Server API error: ${status}`, options);
      this.name = "ServerApiError";
      this.status = status;
    }
  }
  return {
    ServerApiError,
    serverWikiApi: { renameFile: MOCKS.renameFile },
  };
});

import { ServerApiError } from "@/lib/server-api";

import { renameFileAction } from "./wiki-actions";

describe("Wiki Server Action errors", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("uses stable copy for a perimeter rate limit", async () => {
    MOCKS.renameFile.mockRejectedValue(new ServerApiError(429));

    await expect(renameFileAction("doc-1", "Roadmap")).resolves.toEqual({
      error: "Too many requests. Try again in a minute.",
      ok: false,
      status: 429,
    });
  });
});
