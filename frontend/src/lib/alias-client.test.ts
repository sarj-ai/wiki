import { afterEach, describe, expect, it, vi } from "vitest";

import { getRecentDocumentAliases, resolveDocumentAlias } from "@/lib/alias-client";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("alias server client", () => {
  it("records and resolves an alias through the access-checking visit endpoint", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValue(Response.json({ alias: "team-handbook", file_id: "file-1" }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(resolveDocumentAlias("team-handbook", "token")).resolves.toEqual({
      fileId: "file-1",
      status: "found",
    });
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/aliases/visits/team-handbook"),
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("maps permission-filtered recent aliases from API fields to UI fields", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(
        Response.json([
          {
            alias: "team-handbook",
            drive_name: null,
            file_id: "file-1",
            last_visited_at: "2026-08-04T15:00:00Z",
            mime_type: "application/vnd.google-apps.document",
            name: "Team Handbook",
          },
        ]),
      ),
    );

    await expect(getRecentDocumentAliases("token")).resolves.toEqual({
      aliases: [
        {
          alias: "team-handbook",
          driveName: null,
          fileId: "file-1",
          lastVisitedAt: "2026-08-04T15:00:00Z",
          mimeType: "application/vnd.google-apps.document",
          name: "Team Handbook",
        },
      ],
      status: "found",
    });
  });

  it("fails closed when a recent-alias response is malformed", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(Response.json([{ alias: "private-document" }])),
    );

    await expect(getRecentDocumentAliases("token")).resolves.toEqual({ status: "unavailable" });
  });
});
