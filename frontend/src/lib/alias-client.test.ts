import { afterEach, describe, expect, it, vi } from "vitest";

import { getRecentDocumentAliases, resolveDocumentAlias } from "@/lib/alias-client";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("alias server client", () => {
  it("resolves an alias through the access-checking endpoint", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValue(Response.json({ alias: "team-handbook", file_id: "file-1" }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(resolveDocumentAlias("team-handbook", "token")).resolves.toEqual({
      fileId: "file-1",
      status: "found",
    });
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/aliases/team-handbook"),
      expect.not.objectContaining({ method: "POST" }),
    );
    expect(fetchMock.mock.calls[0]?.[1]?.redirect).toBe("manual");
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
            mime_type: "application/vnd.google-apps.document",
            name: "Team Handbook",
          },
        ]),
      ),
    );

    await expect(getRecentDocumentAliases("token", ["team-handbook"])).resolves.toEqual({
      aliases: [
        {
          alias: "team-handbook",
          driveName: null,
          fileId: "file-1",
          mimeType: "application/vnd.google-apps.document",
          name: "Team Handbook",
        },
      ],
      status: "found",
    });
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("/aliases/recent"),
      expect.objectContaining({
        body: JSON.stringify({ aliases: ["team-handbook"] }),
        method: "POST",
      }),
    );
  });

  it("fails closed when a recent-alias response is malformed", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(Response.json([{ alias: "private-document" }])),
    );

    await expect(getRecentDocumentAliases("token", ["private-document"])).resolves.toEqual({
      status: "unavailable",
    });
  });
});
