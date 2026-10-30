import { QueryClient, QueryClientProvider, QueryObserver } from "@tanstack/react-query";
import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";

import type { ActionResult } from "@/lib/action-result";
import { retireMutationSession } from "@/lib/mutation-session";
import { wikiKeys } from "@/lib/wiki-keys";
import type { DriveNode, DriveTree } from "@/types/wiki";
import { MIME_FOLDER, MIME_SHORTCUT } from "@/types/wiki";

import { useRefreshDrive } from "./use-wiki-mutations";

const REFRESH = vi.hoisted(() => vi.fn<() => Promise<ActionResult<DriveTree>>>());
vi.mock("@/app/actions/wiki-actions", () => ({ refreshDriveAction: REFRESH }));

describe("drive refresh listing invalidation", () => {
  let client: QueryClient;
  let root: Root;
  let refresh: ReturnType<typeof useRefreshDrive>;
  const read = vi.fn<(folderId: string) => Promise<DriveNode[]>>();
  const releases: (() => void)[] = [];

  function Probe() {
    refresh = useRefreshDrive();
    return null;
  }

  function observe(folderId: string) {
    client.setQueryData(wikiKeys.folderFiles(folderId), []);
    const observer = new QueryObserver(client, {
      queryFn: async () => await read(folderId),
      queryKey: wikiKeys.folderFiles(folderId),
      staleTime: Infinity,
    });
    releases.push(observer.subscribe(vi.fn()));
  }

  beforeEach(() => {
    vi.useFakeTimers();
    REFRESH.mockReset();
    read.mockReset().mockResolvedValue([]);
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    root = createRoot(document.createElement("div"));
  });

  function mount() {
    flushSync(() =>
      root.render(
        <QueryClientProvider client={client}>
          <Probe />
        </QueryClientProvider>,
      ),
    );
  }

  afterEach(async () => {
    flushSync(() => root.unmount());
    for (const release of releases.splice(0)) release();
    await vi.advanceTimersByTimeAsync(0);
    client.clear();
    vi.useRealTimers();
  });

  it("refreshes its two listings plus an unknown listing without refetching eight unrelated listings", async () => {
    mount();
    for (let index = 0; index < 5; index += 1) {
      const id = `drive-${index}`;
      client.setQueryData(wikiKeys.driveTree(id), tree(id, [`${id}-folder`]));
      observe(id);
      observe(`${id}-folder`);
    }
    observe("unknown-folder");
    REFRESH.mockResolvedValue({ data: tree("drive-0", ["drive-0-folder"]), ok: true });
    await refresh.mutateAsync("drive-0");
    await vi.advanceTimersByTimeAsync(0);
    expect(read.mock.calls.map(([id]) => id).toSorted()).toEqual([
      "drive-0",
      "drive-0-folder",
      "unknown-folder",
    ]);
    expect(REFRESH).toHaveBeenCalledOnce();
    expect(client.getQueryData<DriveTree>(wikiKeys.driveTree("drive-0"))?.filesComplete).toBe(true);
  });

  it("invalidates prior membership and shortcut targets shared with other cached drives", async () => {
    mount();
    client.setQueryData(wikiKeys.driveTree("drive"), tree("drive", ["removed-folder"]));
    client.setQueryData(
      wikiKeys.driveTree("other"),
      tree("other", ["removed-folder", "shared-target", "unrelated"]),
    );
    for (const id of ["removed-folder", "shared-target", "unrelated", "new-folder"]) observe(id);
    const refreshed = tree("drive", ["new-folder"]);
    refreshed.folders.push({
      ...folder("shortcut"),
      mimeType: MIME_SHORTCUT,
      shortcutTargetId: "shared-target",
      shortcutTargetMimeType: MIME_FOLDER,
    });
    REFRESH.mockResolvedValue({ data: refreshed, ok: true });
    await refresh.mutateAsync("drive");
    await vi.advanceTimersByTimeAsync(0);
    expect(read.mock.calls.map(([id]) => id).toSorted()).toEqual([
      "new-folder",
      "removed-folder",
      "shared-target",
    ]);
  });

  it("does not invalidate or republish a refresh after session retirement", async () => {
    mount();
    const pending = Promise.withResolvers<ActionResult<DriveTree>>();
    REFRESH.mockReturnValue(pending.promise);
    observe("drive");
    const operation = refresh.mutateAsync("drive").catch((error: unknown) => error);
    await vi.advanceTimersByTimeAsync(0);
    retireMutationSession(client);
    client.clear();
    pending.resolve({ data: tree("drive", []), ok: true });
    await expect(operation).resolves.toMatchObject({ name: "AbortError" });
    expect(read).not.toHaveBeenCalled();
    expect(client.getQueryCache().getAll()).toHaveLength(0);
  });
});

function tree(driveId: string, folderIds: string[]): DriveTree {
  return { driveId, files: [], filesComplete: true, folders: folderIds.map(folder) };
}

function folder(id: string): DriveNode {
  return {
    id,
    mimeType: MIME_FOLDER,
    modifiedTime: undefined,
    name: id,
    parentId: undefined,
    shortcutInaccessible: false,
    shortcutTargetDriveId: undefined,
    shortcutTargetDriveName: undefined,
    shortcutTargetId: undefined,
    shortcutTargetMimeType: undefined,
  };
}
