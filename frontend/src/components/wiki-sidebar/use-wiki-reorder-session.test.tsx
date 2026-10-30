import type { DragEndEvent } from "@dnd-kit/core";
import { type QueryClient, useQueryClient } from "@tanstack/react-query";
import { StrictMode, useEffect, useLayoutEffect, useRef } from "react";
import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";
import { toast } from "sonner";

import { QueryProvider } from "@/components/query-provider";
import { useRetireDocumentAlias, useSaveDocumentAlias } from "@/hooks/use-document-alias";
import {
  useBulkSetSortOrders,
  useMoveFile,
  useSetDocIcon,
  useSetFolderIcon,
} from "@/hooks/use-wiki-mutations";
import type { ActionResult } from "@/lib/action-result";
import { BrowserUserContext } from "@/lib/browser-user-context";
import { deduplicatedRefresh } from "@/lib/session-client";
import type * as sessionIsolation from "@/lib/session-isolation";
import { clearAuthenticatedSession } from "@/lib/session-isolation";
import { wikiKeys } from "@/lib/wiki-keys";
import { installLocalStorage } from "@/test-stubs/install-local-storage";
import type { DriveNode, DriveTree, FileResponse } from "@/types/wiki";
import { MIME_DOC, MIME_FOLDER } from "@/types/wiki";

import { useDragOverStore } from "./use-drag-over-store";
import { useInFlightMovesStore } from "./use-in-flight-moves";
import { useWikiReorder } from "./use-wiki-reorder";

const ACTIONS = vi.hoisted(() => ({
  aliasRetire: vi.fn<() => Promise<ActionResult<unknown>>>(),
  aliasSave: vi.fn<() => Promise<ActionResult<unknown>>>(),
  bulk: vi.fn<() => Promise<ActionResult<unknown>>>(),
  docIcon: vi.fn<() => Promise<ActionResult<unknown>>>(),
  folderIcon: vi.fn<() => Promise<ActionResult<unknown>>>(),
  move: vi.fn<() => Promise<ActionResult<unknown>>>(),
}));

vi.mock("@/app/actions/wiki-actions", () => ({
  bulkSetSortOrdersAction: ACTIONS.bulk,
  moveFileAction: ACTIONS.move,
  retireDocumentAliasAction: ACTIONS.aliasRetire,
  saveDocumentAliasAction: ACTIONS.aliasSave,
  setDocIconAction: ACTIONS.docIcon,
  setFolderIconAction: ACTIONS.folderIcon,
}));
vi.mock("@/lib/session-client", () => ({
  deduplicatedRefresh: vi.fn<typeof deduplicatedRefresh>().mockResolvedValue("unauthorized"),
}));
vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));
vi.mock("@/lib/session-isolation", async () => ({
  ...(await vi.importActual<typeof sessionIsolation>("@/lib/session-isolation")),
  broadcastSessionClear: vi.fn(),
  subscribeToSessionClear: vi.fn(() => vi.fn()),
}));

const FILE = node("shared-file", MIME_DOC);
const DESTINATION = node("destination", MIME_FOLDER);
const NESTED_FOLDER = node("nested-folder", MIME_FOLDER, DESTINATION.id);
const SIBLING = node("sibling", MIME_DOC, DESTINATION.id);
const RANKS = [
  { driveId: FILE.id, sortOrder: 4 },
  { driveId: NESTED_FOLDER.id, sortOrder: 2 },
  { driveId: SIBLING.id, sortOrder: 1 },
];

interface Hooks {
  aliasRetire: ReturnType<typeof useRetireDocumentAlias>;
  aliasSave: ReturnType<typeof useSaveDocumentAlias>;
  bulk: ReturnType<typeof useBulkSetSortOrders>;
  docIcon: ReturnType<typeof useSetDocIcon>;
  folderIcon: ReturnType<typeof useSetFolderIcon>;
  move: ReturnType<typeof useMoveFile>;
  reorder: ReturnType<typeof useWikiReorder>;
}

describe("sidebar mutation session ownership", () => {
  let root: Root;
  let client: QueryClient;
  let hooks: Hooks;
  const clients = new Set<QueryClient>();
  let seeded: WeakSet<QueryClient>;
  const replace = vi.fn<Location["replace"]>();

  function Probe() {
    const currentClient = useQueryClient();
    hooks = {
      aliasRetire: useRetireDocumentAlias(FILE.id),
      aliasSave: useSaveDocumentAlias(FILE.id),
      bulk: useBulkSetSortOrders(),
      docIcon: useSetDocIcon(),
      folderIcon: useSetFolderIcon(),
      move: useMoveFile(),
      reorder: useWikiReorder(),
    };
    useLayoutEffect(() => {
      client = currentClient;
      clients.add(currentClient);
      if (seeded.has(currentClient)) return;
      seeded.add(currentClient);
      seed(currentClient);
    }, [currentClient]);
    return null;
  }

  function renderProvider(userId: string, strict = false) {
    const provider = (
      <QueryProvider key={userId} userId={userId}>
        <BrowserUserContext value={userId}>
          <Probe />
        </BrowserUserContext>
      </QueryProvider>
    );
    flushSync(() => root.render(strict ? <StrictMode>{provider}</StrictMode> : provider));
  }

  function move(overId = `parent:${DESTINATION.id}`, overData?: Record<string, unknown>) {
    const event = dragEvent(overId, overData);
    flushSync(() => hooks.reorder.onDragStart(event));
    flushSync(() => hooks.reorder.onDragOver(event));
    flushSync(() => hooks.reorder.onDragEnd(event));
  }

  beforeEach(() => {
    vi.useFakeTimers();
    installLocalStorage();
    seeded = new WeakSet();
    for (const action of Object.values(ACTIONS)) action.mockReset();
    ACTIONS.bulk.mockResolvedValue({ data: { updated: 3 }, ok: true });
    vi.mocked(deduplicatedRefresh).mockClear();
    vi.mocked(toast.error).mockClear();
    vi.mocked(toast.success).mockClear();
    replace.mockClear();
    vi.spyOn(window.location, "replace").mockImplementation(replace);
    useDragOverStore.getState().reset();
    useInFlightMovesStore.getState().reset();
    root = createRoot(document.createElement("div"));
  });

  afterEach(async () => {
    flushSync(() => root.unmount());
    await vi.advanceTimersByTimeAsync(0);
    for (const currentClient of clients) currentClient.clear();
    clients.clear();
    useDragOverStore.getState().reset();
    useInFlightMovesStore.getState().reset();
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("derives native same-container hover intent without validating 10,000 sibling IDs per move", () => {
    renderProvider("user-a");
    let itemReads = 0;
    const siblingIds = new Proxy(
      Array.from({ length: 10_000 }, (_, index) =>
        index === 0 ? FILE.id : index === 9_999 ? DESTINATION.id : `item-${index}`,
      ),
      {
        get(target, key, receiver) {
          if (typeof key === "string" && /^\d+$/.test(key)) itemReads += 1;
          const value: unknown = Reflect.get(target, key, receiver);
          return value;
        },
      },
    );
    const event = dragEvent(DESTINATION.id, {
      driveId: "shared-drive",
      isFolder: true,
      sortable: { containerId: "parent:shared-drive", index: 9_999, items: siblingIds },
    });
    event.active.data.current = {
      driveId: "shared-drive",
      sortable: { containerId: "parent:shared-drive", index: 0, items: siblingIds },
    };
    flushSync(() => hooks.reorder.onDragStart(event));
    for (let index = 0; index < 100; index += 1) hooks.reorder.onDragOver(event);
    expect(useDragOverStore.getState().intent).toBe("below");
    expect(itemReads).toBe(0);
    hooks.reorder.onDragCancel();
    expect(ACTIONS.bulk).not.toHaveBeenCalled();
  });

  it("commits native-position drags from the current validated sibling sequence", async () => {
    renderProvider("user-a");
    const event = dragEvent(DESTINATION.id, {
      driveId: "shared-drive",
      sortable: { containerId: "parent:shared-drive", index: 1, items: [FILE.id, DESTINATION.id] },
    });
    event.active.data.current = {
      driveId: "shared-drive",
      sortable: { containerId: "parent:shared-drive", index: 0, items: [FILE.id, DESTINATION.id] },
    };
    flushSync(() => hooks.reorder.onDragStart(event));
    hooks.reorder.onDragOver(event);
    expect(useDragOverStore.getState().intent).toBe("below");
    event.active.data.current = {
      driveId: "shared-drive",
      sortable: { containerId: "parent:shared-drive", index: 0, items: [DESTINATION.id, FILE.id] },
    };
    flushSync(() => hooks.reorder.onDragEnd(event));
    await vi.advanceTimersByTimeAsync(0);
    expect(ACTIONS.bulk).toHaveBeenCalledWith([
      { driveId: FILE.id, sortOrder: 1 },
      { driveId: DESTINATION.id, sortOrder: 2 },
    ]);
  });

  it("moves between validated containers without reading unused sibling arrays", async () => {
    ACTIONS.move.mockResolvedValue({ data: movedFile(), ok: true });
    renderProvider("user-a");
    let itemReads = 0;
    const items = new Proxy(
      Array.from({ length: 1_000 }, (_, index) => `id-${index}`),
      {
        get(target, key, receiver) {
          if (typeof key === "string" && /^\d+$/.test(key)) itemReads += 1;
          const value: unknown = Reflect.get(target, key, receiver);
          return value;
        },
      },
    );
    const event = dragEvent(SIBLING.id, {
      driveId: "shared-drive",
      sortable: { containerId: `parent:${DESTINATION.id}`, items },
    });
    event.active.data.current = {
      driveId: "shared-drive",
      sortable: { containerId: "parent:shared-drive", items },
    };
    flushSync(() => hooks.reorder.onDragStart(event));
    flushSync(() => hooks.reorder.onDragOver(event));
    flushSync(() => hooks.reorder.onDragEnd(event));
    await vi.advanceTimersByTimeAsync(0);
    expect(itemReads).toBe(0);
    expect(ACTIONS.move).toHaveBeenCalledExactlyOnceWith(FILE.id, DESTINATION.id);
  });

  it("admits one provider move for repeated same-file gestures and permits retry after denial", async () => {
    const pending = Promise.withResolvers<ActionResult<unknown>>();
    ACTIONS.move.mockReturnValueOnce(pending.promise);
    renderProvider("user-a");
    move();
    await vi.advanceTimersByTimeAsync(0);
    move(`parent:${NESTED_FOLDER.id}`);
    await vi.advanceTimersByTimeAsync(0);
    expect(ACTIONS.move).toHaveBeenCalledOnce();
    pending.resolve({ error: "Denied", ok: false, status: 403 });
    await vi.advanceTimersByTimeAsync(0);
    expect(useInFlightMovesStore.getState().ids.size).toBe(0);
    ACTIONS.move.mockResolvedValueOnce({ data: movedFile(), ok: true });
    move();
    await vi.advanceTimersByTimeAsync(0);
    expect(ACTIONS.move).toHaveBeenCalledTimes(2);
    expect(toast.success).toHaveBeenCalledOnce();
  });

  it("rejects malformed same-container commit arrays", async () => {
    renderProvider("user-a");
    const event = dragEvent(DESTINATION.id, {
      driveId: "shared-drive",
      sortable: { containerId: "parent:shared-drive", items: [FILE.id, 1] },
    });
    flushSync(() => hooks.reorder.onDragStart(event));
    flushSync(() => hooks.reorder.onDragEnd(event));
    await vi.advanceTimersByTimeAsync(0);
    expect(ACTIONS.bulk).not.toHaveBeenCalled();
    expect(ACTIONS.move).not.toHaveBeenCalled();
  });

  it("does not copy or publish an unchanged 10,000-entry rank list for an unranked move", async () => {
    const pending = Promise.withResolvers<ActionResult<unknown>>();
    ACTIONS.move.mockReturnValue(pending.promise);
    renderProvider("user-a");
    client.setQueryData(
      wikiKeys.sortOrders,
      Array.from({ length: 10_000 }, (_, index) => ({
        driveId: `ranked-${index}`,
        sortOrder: index,
      })),
    );
    const previous = client.getQueryData(wikiKeys.sortOrders);
    if (!Array.isArray(previous)) throw new TypeError("Rank fixture missing");
    const filtered = vi.spyOn(previous, "filter");
    const wrote = vi.spyOn(client, "setQueryData");
    const rankQuery = client.getQueryCache().find({ queryKey: wikiKeys.sortOrders });
    const published = vi.fn();
    const release = client.getQueryCache().subscribe((event) => {
      if (event.type === "updated" && event.query === rankQuery && event.action.type === "success")
        published();
    });
    const operation = hooks.move.mutateAsync({
      fileId: FILE.id,
      newParentFolderId: DESTINATION.id,
      sourceDriveId: "shared-drive",
      sourceParentFolderId: undefined,
    });
    await vi.advanceTimersByTimeAsync(0);
    expect(ACTIONS.move).toHaveBeenCalledOnce();
    expect(filtered).not.toHaveBeenCalled();
    expect(wrote).not.toHaveBeenCalled();
    expect(published).not.toHaveBeenCalled();
    expect(client.getQueryData(wikiKeys.sortOrders)).toBe(previous);
    release();
    pending.resolve({ data: movedFile(), ok: true });
    await operation;
  });

  it("removes all matching ranks in order and restores the prior ranks on a current move denial", async () => {
    const pending = Promise.withResolvers<ActionResult<unknown>>();
    ACTIONS.move.mockReturnValue(pending.promise);
    renderProvider("user-a");
    const leading = { driveId: "leading", sortOrder: 1 };
    const trailing = { driveId: "trailing", sortOrder: 2 };
    const ranks = [
      leading,
      { driveId: FILE.id, sortOrder: 3 },
      trailing,
      { driveId: FILE.id, sortOrder: 4 },
    ];
    client.setQueryData(wikiKeys.sortOrders, ranks);
    const previous = client.getQueryData(wikiKeys.sortOrders);
    const operation = hooks.move.mutateAsync({
      fileId: FILE.id,
      newParentFolderId: DESTINATION.id,
      sourceDriveId: "shared-drive",
      sourceParentFolderId: undefined,
    });
    const failure = operation.catch((error: unknown) => error);
    await vi.advanceTimersByTimeAsync(0);
    expect(client.getQueryData(wikiKeys.sortOrders)).toEqual([leading, trailing]);
    pending.resolve({ error: "Current move denied", ok: false, status: 403 });
    await expect(failure).resolves.toMatchObject({ status: 403 });
    expect(client.getQueryData(wikiKeys.sortOrders)).toEqual(previous);
  });

  it("resets ephemeral state and keeps B's same-file move owned when A settles", async () => {
    const oldAction = Promise.withResolvers<ActionResult<unknown>>();
    const currentAction = Promise.withResolvers<ActionResult<unknown>>();
    ACTIONS.move.mockReturnValueOnce(oldAction.promise).mockReturnValueOnce(currentAction.promise);
    renderProvider("user-a");
    const oldClient = client;
    move();
    await vi.advanceTimersByTimeAsync(0);
    expect(ACTIONS.move).toHaveBeenCalledOnce();
    useDragOverStore.getState().setActive({ containerId: "parent:old", id: "retired-drag" });
    useDragOverStore.getState().setOver({ containerId: "parent:old", id: "old", intent: "into" });

    clearAuthenticatedSession(oldClient, "user-a");
    expect(useInFlightMovesStore.getState().ids.size).toBe(0);
    expect(useDragOverStore.getState().activeId).toBeNull();
    expect(useDragOverStore.getState().overId).toBeNull();
    renderProvider("user-b");
    move();
    await vi.advanceTimersByTimeAsync(0);
    expect(client.isMutating()).toBe(1);

    oldAction.resolve({ error: "Retired move denied", ok: false, status: 401 });
    await vi.advanceTimersByTimeAsync(0);
    expect(useInFlightMovesStore.getState().ids.has(FILE.id)).toBe(true);
    expect(client.isMutating()).toBe(1);
    expect(oldClient.getQueryCache().getAll()).toHaveLength(0);
    expect(toast.error).not.toHaveBeenCalled();
    expect(toast.success).not.toHaveBeenCalled();
    expect(deduplicatedRefresh).not.toHaveBeenCalled();
    expect(replace).not.toHaveBeenCalled();

    currentAction.resolve({ error: "Current move denied", ok: false, status: 403 });
    await vi.advanceTimersByTimeAsync(0);
    expect(useInFlightMovesStore.getState().ids.has(FILE.id)).toBe(false);
    expect(client.getQueryData(wikiKeys.sortOrders)).toEqual(RANKS);
    expect(
      client
        .getQueryData<DriveTree>(wikiKeys.driveTree("shared-drive"))
        ?.files.find((item) => item.id === FILE.id)?.parentId,
    ).toBeUndefined();
    expect(toast.error).toHaveBeenCalledWith(expect.stringContaining("Drive denied this move"));
  });

  it("prevents an old queued drag from dispatching under B's browser epoch", async () => {
    renderProvider("user-a");
    const oldClient = client;
    move();
    expect(ACTIONS.move).not.toHaveBeenCalled();
    renderProvider("user-b");
    client.setQueryData(["replacement-private-data"], "keep");
    await vi.advanceTimersByTimeAsync(0);

    expect(ACTIONS.move).not.toHaveBeenCalled();
    expect(oldClient.getQueryCache().getAll()).toHaveLength(0);
    expect(client.getQueryData(["replacement-private-data"])).toBe("keep");
    expect(useInFlightMovesStore.getState().ids.size).toBe(0);
    expect(toast.error).not.toHaveBeenCalled();
    expect(deduplicatedRefresh).not.toHaveBeenCalled();
    expect(replace).not.toHaveBeenCalled();
  });

  it("prevents a dispatched action's recovery after explicit clear before unmount", async () => {
    const pending = Promise.withResolvers<ActionResult<unknown>>();
    ACTIONS.move.mockReturnValue(pending.promise);
    renderProvider("user-a");
    move();
    await vi.advanceTimersByTimeAsync(0);
    clearAuthenticatedSession(client, "user-a");
    pending.resolve({ error: "Old session denied", ok: false, status: 401 });
    await vi.advanceTimersByTimeAsync(0);

    expect(ACTIONS.move).toHaveBeenCalledOnce();
    expect(client.getQueryCache().getAll()).toHaveLength(0);
    expect(deduplicatedRefresh).not.toHaveBeenCalled();
    expect(replace).not.toHaveBeenCalled();
    expect(toast.error).not.toHaveBeenCalled();
  });

  it("guards queued mutate callbacks and rejects later calls from the retired hook", async () => {
    renderProvider("user-a");
    const oldClient = client;
    const oldHooks = hooks;
    const boundary = Promise.withResolvers<undefined>();
    oldClient.getMutationCache().config.onMutate = async () => await boundary.promise;
    const settled = vi.fn();
    oldHooks.bulk.mutate([{ driveId: FILE.id, sortOrder: 7 }], { onSettled: settled });
    renderProvider("user-b");
    boundary.resolve(undefined);
    await vi.advanceTimersByTimeAsync(0);

    expect(ACTIONS.bulk).not.toHaveBeenCalled();
    expect(settled).not.toHaveBeenCalled();
    await expect(oldHooks.aliasSave.mutateAsync("old-alias")).rejects.toMatchObject({
      name: "AbortError",
    });
    expect(ACTIONS.aliasSave).not.toHaveBeenCalled();
    expect(oldClient.getQueryCache().getAll()).toHaveLength(0);
  });

  it.each(["bulk", "docIcon", "folderIcon", "aliasSave", "aliasRetire"] as const)(
    "does not restore or invalidate retired optimistic %s data",
    async (kind) => {
      const pending = Promise.withResolvers<ActionResult<unknown>>();
      ACTIONS[kind].mockReturnValue(pending.promise);
      renderProvider("user-a");
      const operation = mutate(kind, hooks).catch((error: unknown) => error);
      await vi.advanceTimersByTimeAsync(0);
      expect(ACTIONS[kind]).toHaveBeenCalledOnce();
      const invalidate = vi.spyOn(client, "invalidateQueries");
      clearAuthenticatedSession(client, "user-a");
      pending.resolve({ error: "Retired mutation denied", ok: false, status: 403 });
      await expect(operation).resolves.toMatchObject({ name: "AbortError" });
      expect(client.getQueryCache().getAll()).toHaveLength(0);
      expect(invalidate).not.toHaveBeenCalled();
    },
  );

  it.each(["bulk", "docIcon", "folderIcon", "aliasSave", "aliasRetire"] as const)(
    "captures %s admission before the MutationCache's asynchronous onMutate",
    async (kind) => {
      renderProvider("user-a");
      const oldClient = client;
      const boundary = Promise.withResolvers<undefined>();
      oldClient.getMutationCache().config.onMutate = async () => await boundary.promise;
      const operation = mutate(kind, hooks).catch((error: unknown) => error);
      renderProvider("user-b");
      client.setQueryData(["replacement-private-data"], "keep");
      boundary.resolve(undefined);
      await expect(operation).resolves.toMatchObject({ name: "AbortError" });
      await vi.advanceTimersByTimeAsync(0);

      expect(ACTIONS[kind]).not.toHaveBeenCalled();
      expect(oldClient.getQueryCache().getAll()).toHaveLength(0);
      expect(client.getQueryData(["replacement-private-data"])).toBe("keep");
    },
  );

  it.each(["bulk", "docIcon", "folderIcon"] as const)(
    "preserves current %s optimistic rollback and normal errors",
    async (kind) => {
      ACTIONS[kind].mockResolvedValue({ error: "Current mutation denied", ok: false, status: 403 });
      renderProvider("user-a");
      const key = {
        bulk: wikiKeys.sortOrders,
        docIcon: wikiKeys.docIcons,
        folderIcon: wikiKeys.folderIcons,
      }[kind];
      const previous = client.getQueryData(key);
      const invalidate = vi.spyOn(client, "invalidateQueries");

      await expect(mutate(kind, hooks)).rejects.toMatchObject({ status: 403 });
      expect(client.getQueryData(key)).toEqual(previous);
      expect(invalidate).toHaveBeenCalledWith({ exact: true, queryKey: key });
    },
  );

  it("admits current mutations after Strict Mode setup and preserves destination order", async () => {
    ACTIONS.move.mockResolvedValue({ data: movedFile(), ok: true });
    renderProvider("user-a", true);
    move(SIBLING.id, {
      driveId: "shared-drive",
      parentFolderId: DESTINATION.id,
      sortable: { containerId: `parent:${DESTINATION.id}`, items: [SIBLING.id, NESTED_FOLDER.id] },
    });
    await vi.advanceTimersByTimeAsync(0);

    expect(ACTIONS.move).toHaveBeenCalledOnce();
    expect(ACTIONS.bulk).toHaveBeenCalledWith([
      { driveId: SIBLING.id, sortOrder: 1 },
      { driveId: FILE.id, sortOrder: 2 },
      { driveId: NESTED_FOLDER.id, sortOrder: 3 },
    ]);
    expect(
      client
        .getQueryData<DriveTree>(wikiKeys.driveTree("shared-drive"))
        ?.files.find((item) => item.id === FILE.id)?.parentId,
    ).toBe(DESTINATION.id);
    expect(useInFlightMovesStore.getState().ids.size).toBe(0);
    expect(toast.success).toHaveBeenCalledOnce();
    expect(toast.error).not.toHaveBeenCalled();
  });

  it("admits a child's current mutation during Strict Mode passive effect replay", async () => {
    ACTIONS.move.mockResolvedValue({ data: movedFile(), ok: true });
    const operations: Promise<unknown>[] = [];
    function StartMove() {
      const moveFile = useMoveFile();
      const start = useRef(moveFile.mutateAsync);
      useEffect(() => {
        operations.push(
          start
            .current({
              fileId: FILE.id,
              newParentFolderId: DESTINATION.id,
              sourceDriveId: "shared-drive",
              sourceParentFolderId: undefined,
            })
            .catch((error: unknown) => error),
        );
      }, []);
      return null;
    }
    flushSync(() =>
      root.render(
        <StrictMode>
          <QueryProvider userId="user-a">
            <Probe />
            <StartMove />
          </QueryProvider>
        </StrictMode>,
      ),
    );
    await vi.advanceTimersByTimeAsync(0);

    expect(operations).toHaveLength(2);
    expect(ACTIONS.move).toHaveBeenCalledOnce();
    await expect(operations[0]).resolves.toMatchObject({ name: "AbortError" });
    await expect(operations[1]).resolves.toMatchObject({ result: movedFile() });
    expect(
      client
        .getQueryData<DriveTree>(wikiKeys.driveTree("shared-drive"))
        ?.files.find((item) => item.id === FILE.id)?.parentId,
    ).toBe(DESTINATION.id);
  });
});

function movedFile(): FileResponse {
  return {
    canEdit: true,
    driveId: "shared-drive",
    id: FILE.id,
    mimeType: MIME_DOC,
    modifiedTime: undefined,
    name: FILE.name,
    parentFolderId: DESTINATION.id,
  };
}

async function mutate(
  kind: "aliasRetire" | "aliasSave" | "bulk" | "docIcon" | "folderIcon",
  hooks: Hooks,
) {
  switch (kind) {
    case "aliasRetire":
      return await hooks.aliasRetire.mutateAsync();
    case "aliasSave":
      return await hooks.aliasSave.mutateAsync("saved-alias");
    case "bulk":
      return await hooks.bulk.mutateAsync([{ driveId: FILE.id, sortOrder: 7 }]);
    case "docIcon":
      return await hooks.docIcon.mutateAsync({ driveId: FILE.id, icon: "new" });
    case "folderIcon":
      return await hooks.folderIcon.mutateAsync({ folderDriveId: DESTINATION.id, icon: "new" });
  }
}

function seed(client: QueryClient): void {
  client.setQueryData<DriveTree>(wikiKeys.driveTree("shared-drive"), {
    driveId: "shared-drive",
    files: [FILE, SIBLING],
    filesComplete: true,
    folders: [DESTINATION, NESTED_FOLDER],
  });
  client.setQueryData(wikiKeys.sortOrders, RANKS);
  client.setQueryData(wikiKeys.docIcons, [{ driveId: FILE.id, icon: "old", coverUrl: "cover" }]);
  client.setQueryData(wikiKeys.folderIcons, [{ folderDriveId: DESTINATION.id, icon: "old" }]);
}

function dragEvent(overId: string, overData?: Record<string, unknown>): DragEndEvent {
  return {
    active: {
      data: {
        current: {
          driveId: "shared-drive",
          parentFolderId: undefined,
          sortable: { containerId: "parent:shared-drive", items: [FILE.id] },
        },
      },
      id: FILE.id,
      rect: { current: { initial: null, translated: null } },
    },
    activatorEvent: new Event("pointerdown"),
    collisions: null,
    delta: { x: 0, y: 0 },
    over: {
      data: { current: overData ?? { driveId: "shared-drive", parentFolderId: DESTINATION.id } },
      disabled: false,
      id: overId,
      rect: { bottom: 10, height: 10, left: 0, right: 10, top: 0, width: 10 },
    },
  };
}

function node(id: string, mimeType: string, parentId?: string): DriveNode {
  return {
    id,
    mimeType,
    modifiedTime: undefined,
    name: id,
    parentId,
    shortcutInaccessible: false,
    shortcutTargetDriveId: undefined,
    shortcutTargetDriveName: undefined,
    shortcutTargetId: undefined,
    shortcutTargetMimeType: undefined,
  };
}
