import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";

import type { ActionResult } from "@/lib/action-result";
import { activateMutationSession, retireMutationSession } from "@/lib/mutation-session";
import { wikiKeys } from "@/lib/wiki-keys";
import type {
  DocIcon,
  DriveNode,
  DriveTree,
  FileDetail,
  Preferences,
  SortOrder,
} from "@/types/wiki";
import { MIME_DOC, MIME_SHORTCUT } from "@/types/wiki";

import {
  useBulkSetSortOrders,
  useRenameFile,
  useSetDocIcon,
  useSetPreferences,
} from "./use-wiki-mutations";

const ACTIONS = vi.hoisted(() => ({
  bulk: vi.fn<() => Promise<ActionResult<unknown>>>(),
  icon: vi.fn<() => Promise<ActionResult<unknown>>>(),
  preferences: vi.fn<() => Promise<ActionResult<Preferences>>>(),
  rename: vi.fn<() => Promise<ActionResult<FileDetail>>>(),
}));

vi.mock("@/app/actions/wiki-actions", () => ({
  bulkSetSortOrdersAction: ACTIONS.bulk,
  renameFileAction: ACTIONS.rename,
  setDocIconAction: ACTIONS.icon,
  setPreferencesAction: ACTIONS.preferences,
}));

describe("round-six mutation lifecycle fixtures", () => {
  let root: Root;
  let client: QueryClient;
  let icon: ReturnType<typeof useSetDocIcon>;
  let bulk: ReturnType<typeof useBulkSetSortOrders>;
  let preferences: ReturnType<typeof useSetPreferences>;
  let rename: ReturnType<typeof useRenameFile>;

  function Probe() {
    icon = useSetDocIcon();
    bulk = useBulkSetSortOrders();
    preferences = useSetPreferences();
    rename = useRenameFile();
    return null;
  }

  beforeEach(() => {
    vi.useFakeTimers();
    for (const action of Object.values(ACTIONS)) action.mockReset();
    client = new QueryClient({
      defaultOptions: {
        mutations: { gcTime: Infinity, retry: false },
        queries: { gcTime: Infinity, retry: false },
      },
    });
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
    retireMutationSession(client);
    client.clear();
    await vi.advanceTimersByTimeAsync(0);
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("cancels only the exact stale icon read while leaving the admitted provider action pending", async () => {
    render();
    client.setQueryData<DocIcon[]>(wikiKeys.docIcons, [
      { driveId: "file", icon: "old", coverUrl: undefined },
    ]);
    const staleRead = Promise.withResolvers<DocIcon[]>();
    let readSignal: AbortSignal | undefined;
    const read = client
      .query({
        queryKey: wikiKeys.docIcons,
        queryFn: async ({ signal }) => {
          readSignal = signal;
          return await staleRead.promise;
        },
      })
      .catch((error: unknown) => error);
    const action = Promise.withResolvers<ActionResult<unknown>>();
    ACTIONS.icon.mockReturnValue(action.promise);
    const write = icon.mutateAsync({ driveId: "file", icon: "pending" });
    await vi.advanceTimersByTimeAsync(0);
    expect(readSignal?.aborted).toBe(true);
    expect(ACTIONS.icon).toHaveBeenCalledOnce();
    expect(icon.isPending).toBe(true);
    staleRead.resolve([{ driveId: "file", icon: "stale", coverUrl: undefined }]);
    await read;
    expect(client.getQueryData<DocIcon[]>(wikiKeys.docIcons)?.[0]?.icon).toBe("pending");
    action.resolve({ ok: true, data: {} });
    await write;
  });

  it("reconciles once after eight overlapping icon writes, retaining each independent intent", async () => {
    render();
    client.setQueryData<DocIcon[]>(wikiKeys.docIcons, []);
    const invalidate = vi.spyOn(client, "invalidateQueries");
    const actions = Array.from({ length: 8 }, () => Promise.withResolvers<ActionResult<unknown>>());
    for (const action of actions) ACTIONS.icon.mockReturnValueOnce(action.promise);
    const writes = actions.map(
      async (_action, index) =>
        await icon.mutateAsync({ driveId: `file-${index}`, icon: "pending" }),
    );
    await vi.advanceTimersByTimeAsync(0);
    for (const action of actions.slice(0, 7)) action.resolve({ ok: true, data: {} });
    await Promise.all(writes.slice(0, 7));
    expect(invalidate).not.toHaveBeenCalled();
    actions[7]?.resolve({ ok: true, data: {} });
    await writes[7];
    expect(invalidate).toHaveBeenCalledOnce();
    expect(client.getQueryData<DocIcon[]>(wikiKeys.docIcons)).toHaveLength(8);
  });

  it("retirement during asynchronous read cancellation cleans up before same-client reactivation", async () => {
    render();
    client.setQueryData<DocIcon[]>(wikiKeys.docIcons, [
      { driveId: "file", icon: "old", coverUrl: undefined },
    ]);
    const cancellation = Promise.withResolvers<undefined>();
    const cancel = vi.spyOn(client, "cancelQueries").mockReturnValueOnce(cancellation.promise);
    const old = icon
      .mutateAsync({ driveId: "file", icon: "old-epoch" })
      .catch((error: unknown) => error);
    await vi.advanceTimersByTimeAsync(0);
    retireMutationSession(client);
    activateMutationSession(client);
    cancellation.resolve(undefined);
    await expect(old).resolves.toMatchObject({ name: "AbortError" });
    expect(ACTIONS.icon).not.toHaveBeenCalled();
    cancel.mockRestore();
    ACTIONS.icon.mockResolvedValue({ ok: true, data: {} });
    const invalidate = vi.spyOn(client, "invalidateQueries");
    await icon.mutateAsync({ driveId: "file", icon: "new-epoch" });
    expect(invalidate).toHaveBeenCalledOnce();
    expect(client.getQueryData<DocIcon[]>(wikiKeys.docIcons)?.[0]?.icon).toBe("new-epoch");
  });

  it("cleans up pending ownership on retirement even when onSettled is skipped", async () => {
    render();
    client.setQueryData<SortOrder[]>(wikiKeys.sortOrders, [{ driveId: "file", sortOrder: 1 }]);
    const pending = Promise.withResolvers<ActionResult<unknown>>();
    ACTIONS.bulk.mockReturnValueOnce(pending.promise);
    const old = bulk
      .mutateAsync([{ driveId: "file", sortOrder: 2 }])
      .catch((error: unknown) => error);
    await vi.advanceTimersByTimeAsync(0);
    retireMutationSession(client);
    activateMutationSession(client);
    client.setQueryData<SortOrder[]>(wikiKeys.sortOrders, [{ driveId: "file", sortOrder: 9 }]);
    ACTIONS.bulk.mockResolvedValueOnce({ ok: true, data: { updated: 1 } });
    const invalidate = vi.spyOn(client, "invalidateQueries");
    await bulk.mutateAsync([{ driveId: "file", sortOrder: 10 }]);
    pending.resolve({ ok: false, error: "Old denial", status: 403 });
    await expect(old).resolves.toMatchObject({ name: "AbortError" });
    expect(invalidate).toHaveBeenCalledOnce();
    expect(client.getQueryData<SortOrder[]>(wikiKeys.sortOrders)?.[0]?.sortOrder).toBe(10);
  });

  it("stages preferences immediately, serializes provider writes and rolls back only the denied field", async () => {
    render();
    client.setQueryData<Preferences>(wikiKeys.preferences, {
      showNonDocFiles: true,
      hiddenWorkspaceIds: [],
    });
    const first = Promise.withResolvers<ActionResult<Preferences>>();
    const second = Promise.withResolvers<ActionResult<Preferences>>();
    ACTIONS.preferences.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    const one = preferences.mutateAsync({ showNonDocFiles: false });
    const two = preferences
      .mutateAsync({ hiddenWorkspaceIds: ["drive"] })
      .catch((error: unknown) => error);
    await vi.advanceTimersByTimeAsync(0);
    expect(ACTIONS.preferences).toHaveBeenCalledOnce();
    expect(preferences.isPending).toBe(true);
    expect(client.getQueryData(wikiKeys.preferences)).toEqual({
      showNonDocFiles: false,
      hiddenWorkspaceIds: ["drive"],
    });
    first.resolve({ ok: true, data: { showNonDocFiles: false, hiddenWorkspaceIds: [] } });
    await one;
    await vi.advanceTimersByTimeAsync(0);
    expect(ACTIONS.preferences).toHaveBeenCalledTimes(2);
    expect(client.getQueryData<Preferences>(wikiKeys.preferences)?.hiddenWorkspaceIds).toEqual([
      "drive",
    ]);
    second.resolve({ ok: false, error: "Denied", status: 403 });
    await expect(two).resolves.toMatchObject({ status: 403 });
    expect(client.getQueryData(wikiKeys.preferences)).toEqual({
      showNonDocFiles: false,
      hiddenWorkspaceIds: [],
    });
  });

  it("preserves fresh preference defaults and unknown fields after a pending denial", async () => {
    render();
    client.setQueryData(wikiKeys.preferences, {
      showNonDocFiles: true,
      hiddenWorkspaceIds: [],
      extra: "old",
    });
    const pending = Promise.withResolvers<ActionResult<Preferences>>();
    ACTIONS.preferences.mockReturnValueOnce(pending.promise);
    const write = preferences
      .mutateAsync({ showNonDocFiles: false })
      .catch((error: unknown) => error);
    await vi.advanceTimersByTimeAsync(0);
    await client.query({
      queryKey: wikiKeys.preferences,
      queryFn: () => ({ showNonDocFiles: true, hiddenWorkspaceIds: ["fresh"], extra: "fresh" }),
    });
    pending.resolve({ ok: false, error: "Denied", status: 403 });
    await expect(write).resolves.toMatchObject({ status: 403 });
    expect(client.getQueryData(wikiKeys.preferences)).toEqual({
      showNonDocFiles: true,
      hiddenWorkspaceIds: ["fresh"],
      extra: "fresh",
    });
  });

  it("patches a confirmed rename before read reconciliation, preserving shortcut and permission metadata", async () => {
    render();
    const node: DriveNode = {
      id: "shortcut",
      name: "old",
      mimeType: MIME_SHORTCUT,
      parentId: "folder",
      modifiedTime: undefined,
      shortcutInaccessible: false,
      shortcutTargetId: "target",
      shortcutTargetMimeType: MIME_DOC,
      shortcutTargetDriveId: "target-drive",
      shortcutTargetDriveName: "Target Drive",
    };
    const file: FileDetail = {
      canEdit: false,
      driveId: "drive",
      id: node.id,
      name: node.name,
      mimeType: node.mimeType,
      modifiedTime: undefined,
      parentFolderId: "folder",
      breadcrumb: [],
      driveName: "Drive",
    };
    client.setQueryData<DriveTree>(wikiKeys.driveTree("drive"), {
      driveId: "drive",
      files: [node],
      filesComplete: false,
      folders: [],
    });
    client.setQueryData(wikiKeys.folderFiles("folder"), [node]);
    client.setQueryData(wikiKeys.file(node.id), file);
    const pending = Promise.withResolvers<ActionResult<FileDetail>>();
    ACTIONS.rename.mockReturnValueOnce(pending.promise);
    const write = rename.mutateAsync({ fileId: node.id, name: "requested" });
    await vi.advanceTimersByTimeAsync(0);
    expect(client.getQueryData<DriveTree>(wikiKeys.driveTree("drive"))?.files[0]?.name).toBe("old");
    pending.resolve({ ok: true, data: { ...file, canEdit: true, name: "confirmed" } });
    await write;
    expect(client.getQueryData<DriveTree>(wikiKeys.driveTree("drive"))?.files[0]).toMatchObject({
      name: "confirmed",
      shortcutTargetId: "target",
      shortcutTargetMimeType: MIME_DOC,
    });
    expect(client.getQueryData<FileDetail>(wikiKeys.file(node.id))).toMatchObject({
      name: "confirmed",
      canEdit: false,
      breadcrumb: [],
    });
    expect(client.getQueryData<DriveTree>(wikiKeys.driveTree("drive"))?.filesComplete).toBe(false);
  });
});
