import { beforeEach, describe, expect, it, vi } from "vitest";

import { WIKI_READ_API } from "@/lib/wiki-read-api";

const fetchMock = vi.fn<typeof fetch>();

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

describe("WIKI_READ_API", () => {
  it("validates and transforms file metadata through the authenticated GET route", async () => {
    fetchMock.mockResolvedValue(
      Response.json({
        id: "file-1",
        name: "Plan",
        mime_type: "application/vnd.google-apps.document",
        can_edit: true,
        can_add_children: false,
        drive_id: "drive-1",
        parent_folder_id: "folder-1",
        modified_time: null,
        description: null,
        breadcrumb: [],
        drive_name: "Engineering",
      }),
    );
    await expect(WIKI_READ_API.getFile("file-1")).resolves.toMatchObject({
      id: "file-1",
      name: "Plan",
      driveId: "drive-1",
      canEdit: true,
    });
    const [url, options] = fetchMock.mock.calls[0] ?? [];
    expect(url).toBe("/api/wiki/files/file-1");
    expect(options?.headers).toMatchObject({ "x-wiki-read": "1" });
  });

  it("encodes search parameters and forwards cancellation", async () => {
    fetchMock.mockResolvedValue(Response.json([]));
    const controller = new AbortController();
    await expect(
      WIKI_READ_API.driveSearch("launch & learn", 10, controller.signal),
    ).resolves.toEqual([]);
    const [url, options] = fetchMock.mock.calls[0] ?? [];
    expect(url).toBe("/api/wiki/drive-search?q=launch+%26+learn&limit_per_drive=10");
    expect(options?.signal).toBeInstanceOf(AbortSignal);
    controller.abort();
    expect(fetchMock.mock.calls[0]?.[1]?.signal?.aborted).toBe(true);
  });

  it("starts independent reads together without an action queue", async () => {
    const resolvers: ((response: Response) => void)[] = [];
    fetchMock.mockImplementation(
      async () =>
        await new Promise<Response>((resolve) => {
          resolvers.push(resolve);
        }),
    );
    const drives = WIKI_READ_API.getDrives();
    const folders = WIKI_READ_API.listFolderIcons();
    const sorts = WIKI_READ_API.listSortOrders();
    expect(fetchMock).toHaveBeenCalledTimes(3);
    for (const resolve of resolvers) resolve(Response.json([]));
    await expect(Promise.all([drives, folders, sorts])).resolves.toEqual([[], [], []]);
  });

  it("rejects malformed successful responses", async () => {
    fetchMock.mockResolvedValue(Response.json({ id: 123 }));
    await expect(WIKI_READ_API.getFile("file-1")).rejects.toThrow();
  });
});
