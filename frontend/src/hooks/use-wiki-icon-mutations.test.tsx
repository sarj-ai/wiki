import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";

import type { ActionResult } from "@/lib/action-result";
import { retireMutationSession } from "@/lib/mutation-session";
import { wikiKeys } from "@/lib/wiki-keys";
import type { DocIcon, FolderIcon } from "@/types/wiki";

import { useSetDocIcon, useSetFolderIcon } from "./use-wiki-mutations";

const ACTIONS = vi.hoisted(() => ({
  doc: vi.fn<() => Promise<ActionResult<unknown>>>(),
  folder: vi.fn<() => Promise<ActionResult<unknown>>>(),
}));
vi.mock("@/app/actions/wiki-actions", () => ({
  setDocIconAction: ACTIONS.doc,
  setFolderIconAction: ACTIONS.folder,
}));

describe("icon optimistic overlay work", () => {
  let root: Root;
  let client: QueryClient;
  let doc: ReturnType<typeof useSetDocIcon>;
  let folder: ReturnType<typeof useSetFolderIcon>;

  function Probe() {
    doc = useSetDocIcon();
    folder = useSetFolderIcon();
    return null;
  }

  beforeEach(() => {
    vi.useFakeTimers();
    ACTIONS.doc.mockReset();
    ACTIONS.folder.mockReset();
    client = new QueryClient({ defaultOptions: { mutations: { retry: false, gcTime: Infinity } } });
    root = createRoot(document.createElement("div"));
  });

  function render() {
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
    await vi.advanceTimersByTimeAsync(0);
    client.clear();
    vi.useRealTimers();
  });

  it("visits a 10,000-entry target-at-end icon overlay once and preserves its cover", async () => {
    render();
    let idReads = 0;
    const previous = Array.from({ length: 10_000 }, (_, index): DocIcon => ({
      get driveId() {
        idReads++;
        return `file-${index}`;
      },
      icon: "📄",
      coverUrl: `cover-${index}`,
    }));
    client.setQueryData(wikiKeys.docIcons, previous);
    const pending = Promise.withResolvers<ActionResult<unknown>>();
    ACTIONS.doc.mockReturnValue(pending.promise);
    const operation = doc.mutateAsync({ driveId: "file-9999", icon: "🌟" });
    await vi.advanceTimersByTimeAsync(0);
    expect(idReads).toBe(10_001);
    const current = client.getQueryData<DocIcon[]>(wikiKeys.docIcons);
    expect(current).toHaveLength(10_000);
    expect(current?.at(-1)).toEqual({ driveId: "file-9999", icon: "🌟", coverUrl: "cover-9999" });
    expect(current?.[0]).toBe(previous[0]);
    pending.resolve({ ok: true, data: {} });
    await operation;
    expect(ACTIONS.doc).toHaveBeenCalledOnce();
  });

  it("retains first-match cover and removes all duplicate entries in their existing order", async () => {
    render();
    const previous: DocIcon[] = [
      { driveId: "target", icon: "first", coverUrl: "first-cover" },
      { driveId: "keep", icon: "📄", coverUrl: undefined },
      { driveId: "target", icon: "second", coverUrl: "second-cover" },
    ];
    client.setQueryData(wikiKeys.docIcons, previous);
    ACTIONS.doc.mockResolvedValue({ ok: true, data: {} });
    await doc.mutateAsync({ driveId: "target", icon: null });
    expect(client.getQueryData(wikiKeys.docIcons)).toEqual([
      previous[1],
      { driveId: "target", icon: undefined, coverUrl: "first-cover" },
    ]);
  });

  it("restores current-session normal folder failures and does not restore retired caches", async () => {
    render();
    const previous: FolderIcon[] = [{ folderDriveId: "folder", icon: "📂" }];
    client.setQueryData(wikiKeys.folderIcons, previous);
    ACTIONS.folder.mockResolvedValueOnce({ ok: false, error: "Denied", status: 403 });
    await expect(folder.mutateAsync({ folderDriveId: "folder", icon: "🌟" })).rejects.toThrow(
      "Denied",
    );
    expect(client.getQueryData(wikiKeys.folderIcons)).toEqual(previous);

    const pending = Promise.withResolvers<ActionResult<unknown>>();
    ACTIONS.folder.mockReturnValueOnce(pending.promise);
    const operation = folder
      .mutateAsync({ folderDriveId: "folder", icon: "🌟" })
      .catch((error: unknown) => error);
    await vi.advanceTimersByTimeAsync(0);
    retireMutationSession(client);
    client.clear();
    pending.resolve({ ok: false, error: "Retired", status: 403 });
    await expect(operation).resolves.toMatchObject({ name: "AbortError" });
    expect(client.getQueryCache().getAll()).toHaveLength(0);
  });
});
