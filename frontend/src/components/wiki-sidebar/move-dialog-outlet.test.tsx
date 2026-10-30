import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode, useRef } from "react";
import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";

import { Sidebar, SidebarProvider, SidebarTrigger, useSidebar } from "@/components/ui/sidebar";
import { useDriveTree } from "@/hooks/use-wiki-tree";
import type { ActionResult } from "@/lib/action-result";
import { ApiError } from "@/lib/api-error";
import { BrowserUserContext } from "@/lib/browser-user-context";
import { activateMutationSession, retireMutationSession } from "@/lib/mutation-session";
import { wikiKeys } from "@/lib/wiki-keys";
import type { DriveNode, DriveSummary, DriveTree, FileDetail, FileResponse } from "@/types/wiki";
import { MIME_DOC } from "@/types/wiki";

import { ItemMenu, type ItemMenuProps } from "./item-menu";
import { MoveDialogOutletProvider, useMoveDialogOutlet } from "./move-dialog-outlet";

const ACTIONS = vi.hoisted(() => ({
  copy: vi.fn(),
  delete: vi.fn(),
  rename: vi.fn(),
  move: vi.fn<() => Promise<ActionResult<FileResponse>>>(),
}));
const READS = vi.hoisted(() => ({
  drives: vi.fn<() => Promise<DriveSummary[]>>(),
  folders: vi.fn<() => Promise<DriveTree>>(),
}));
vi.mock("@/app/actions/wiki-actions", () => ({
  copyFileAction: ACTIONS.copy,
  deleteFileAction: ACTIONS.delete,
  moveFileAction: ACTIONS.move,
  renameFileAction: ACTIONS.rename,
}));
vi.mock("@/lib/wiki-read-api", () => ({
  WIKI_READ_API: { getDrives: READS.drives, getDriveFolders: READS.folders },
}));
vi.mock("next/navigation", () => ({
  usePathname: () => "/",
  useRouter: () => ({ push: vi.fn() }),
}));
vi.mock("next/dynamic", async () => ({
  default: await vi.importActual("next/dist/shared/lib/app-dynamic"),
}));
vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

const ITEM: ItemMenuProps = {
  currentName: "Move draft",
  driveId: "source",
  fileId: "file",
  isFolder: false,
  parentFolderId: undefined,
};
const DRIVES: DriveSummary[] = [
  { id: "source", isPersonal: false, name: "Source" },
  { id: "destination", isPersonal: false, name: "Destination" },
];
const FILE_NODE: DriveNode = {
  id: ITEM.fileId,
  mimeType: MIME_DOC,
  modifiedTime: undefined,
  name: ITEM.currentName,
  parentId: undefined,
  shortcutInaccessible: false,
  shortcutTargetDriveId: undefined,
  shortcutTargetDriveName: undefined,
  shortcutTargetId: undefined,
  shortcutTargetMimeType: undefined,
};
const SOURCE: DriveTree = {
  driveId: "source",
  files: [FILE_NODE],
  filesComplete: true,
  folders: [],
};
const FILE_DETAIL: FileDetail = {
  breadcrumb: [],
  canEdit: true,
  driveId: ITEM.driveId,
  driveName: "Source",
  id: ITEM.fileId,
  mimeType: MIME_DOC,
  modifiedTime: undefined,
  name: ITEM.currentName,
  parentFolderId: undefined,
};

function SourceChildren() {
  const query = useDriveTree("source");
  return <span data-source-child>{query.data?.files[0]?.name ?? "Source loading"}</span>;
}

function ResponsiveMode() {
  const { isMobile } = useSidebar();
  return <output>{isMobile ? "Mobile" : "Desktop"}</output>;
}

function ReopenMove({ item }: { item: ItemMenuProps }) {
  const outlet = useMoveDialogOutlet();
  const opener = useRef<HTMLButtonElement | null>(null);
  return (
    <button onClick={() => outlet?.openItem(item, opener)} ref={opener} type="button">
      Reopen Move
    </button>
  );
}

function Harness({ item, userId }: { item: ItemMenuProps; userId: string }) {
  return (
    <BrowserUserContext value={userId}>
      <SidebarProvider>
        <SidebarTrigger />
        <ResponsiveMode />
        <MoveDialogOutletProvider userId={userId}>
          <ReopenMove item={item} />
          <Sidebar>
            <SourceChildren />
            <ItemMenu {...item} />
          </Sidebar>
        </MoveDialogOutletProvider>
      </SidebarProvider>
    </BrowserUserContext>
  );
}

describe("admitted Move ownership above the responsive sidebar", () => {
  let client: QueryClient;
  let host: HTMLDivElement;
  let root: Root;
  let initialWidth: number;
  let animationStyle: HTMLStyleElement | null;

  beforeEach(() => {
    vi.useFakeTimers();
    animationStyle = null;
    vi.clearAllMocks();
    READS.drives.mockResolvedValue(DRIVES);
    READS.folders.mockResolvedValue(SOURCE);
    initialWidth = window.innerWidth;
    client = new QueryClient({
      defaultOptions: {
        mutations: { retry: false, gcTime: Infinity },
        queries: { retry: false, gcTime: Infinity },
      },
    });
    client.setQueryData(wikiKeys.drives, DRIVES);
    host = document.createElement("div");
    document.body.append(host);
    root = createRoot(host);
  });

  afterEach(async () => {
    flushSync(() => root.unmount());
    retireMutationSession(client);
    client.clear();
    await vi.advanceTimersByTimeAsync(0);
    animationStyle?.remove();
    host.remove();
    Object.defineProperty(window, "innerWidth", { configurable: true, value: initialWidth });
    vi.useRealTimers();
  });

  function render({
    width = 1200,
    item = ITEM,
    userId = "user-a",
  }: { width?: number; item?: ItemMenuProps; userId?: string } = {}) {
    Object.defineProperty(window, "innerWidth", { configurable: true, value: width });
    flushSync(() =>
      root.render(
        <StrictMode>
          <QueryClientProvider client={client}>
            <Harness item={item} userId={userId} />
          </QueryClientProvider>
        </StrictMode>,
      ),
    );
  }

  function toggle(): HTMLButtonElement {
    const element = host.querySelector('[data-slot="sidebar-trigger"]');
    if (!(element instanceof HTMLButtonElement)) throw new TypeError("Sidebar toggle missing");
    return element;
  }

  function holdExitAnimation() {
    animationStyle = document.createElement("style");
    animationStyle.textContent = `
      @keyframes move-enter { from { opacity: 0; } to { opacity: 1; } }
      @keyframes move-exit { from { opacity: 1; } to { opacity: 0; } }
      [data-slot="dialog-content"][data-state="open"] { animation-name: move-enter; }
      [data-slot="dialog-content"][data-state="closed"] {
        animation-name: move-exit;
        animation-duration: 1s;
      }
    `;
    document.head.append(animationStyle);
  }

  function moveDialog(): HTMLElement {
    const dialog = [...document.querySelectorAll('[role="dialog"]')].find((element) =>
      element.textContent.includes(`Move “${ITEM.currentName}”`),
    );
    if (!(dialog instanceof HTMLElement)) throw new TypeError("Move dialog missing");
    return dialog;
  }

  async function openMove() {
    const opener = document.querySelector(
      `button[aria-label="Actions for ${CSS.escape(ITEM.currentName)}"]`,
    );
    if (!(opener instanceof HTMLButtonElement)) throw new TypeError("Actions trigger missing");
    flushSync(() => {
      opener.focus();
      opener.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, key: "Enter" }));
    });
    await vi.waitFor(() => expect(document.querySelector('[role="menu"]')).not.toBeNull());
    const action = [...document.querySelectorAll('[role="menuitem"]')].find(
      (element) => element.textContent === "Move to…",
    );
    if (!(action instanceof HTMLElement)) throw new TypeError("Move action missing");
    flushSync(() => action.click());
    await vi.waitFor(() => expect(document.body).toHaveTextContent(`Move “${ITEM.currentName}”`));
    await vi.advanceTimersByTimeAsync(0);
    return opener;
  }

  it("keeps a closed initial mobile tree unmounted with zero child or dialog reads", async () => {
    render({ width: 390 });
    await vi.advanceTimersByTimeAsync(250);
    expect(host).toHaveTextContent("Mobile");
    expect(document.querySelector("[data-source-child]")).toBeNull();
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    expect(READS.folders).not.toHaveBeenCalled();
    expect(READS.drives).not.toHaveBeenCalled();
    expect(client.getMutationCache().getAll()).toHaveLength(0);
    assertNoWrites();
  });

  it("preserves the actual dialog and selected destination across desktop to closed mobile, then cancels without a write", async () => {
    render();
    await vi.advanceTimersByTimeAsync(0);
    const opener = await openMove();
    const initialSourceReads = READS.folders.mock.calls.length;
    expect(initialSourceReads).toBeGreaterThan(0);
    const dialog = document.querySelector('[role="dialog"]');
    flushSync(() => button("Destination").click());
    expect(button("Move")).toBeEnabled();
    render({ width: 390 });
    await vi.advanceTimersByTimeAsync(0);
    expect(host).toHaveTextContent("Mobile");
    expect(opener.isConnected).toBe(false);
    expect(document.querySelector("[data-source-child]")).toBeNull();
    expect(document.querySelector('[role="dialog"]')).toBe(dialog);
    expect(button("Move")).toBeEnabled();
    expect(READS.folders).toHaveBeenCalledTimes(initialSourceReads);
    expect(READS.drives).not.toHaveBeenCalled();
    flushSync(() => button("Cancel").click());
    await vi.advanceTimersByTimeAsync(250);
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    expect(toggle()).toHaveFocus();
    assertNoWrites();
  });

  it("preserves the actual dialog and selection from an open mobile Sheet to desktop", async () => {
    render({ width: 390 });
    flushSync(() => toggle().click());
    await vi.advanceTimersByTimeAsync(0);
    await openMove();
    flushSync(() => button("Destination").click());
    const dialog = [...document.querySelectorAll('[role="dialog"]')].find((element) =>
      element.textContent.includes(`Move “${ITEM.currentName}”`),
    );
    render({ width: 1200 });
    await vi.advanceTimersByTimeAsync(0);
    expect(host).toHaveTextContent("Desktop");
    expect(document.querySelectorAll('[role="dialog"]')).toHaveLength(1);
    expect(document.querySelector('[role="dialog"]')).toBe(dialog);
    expect(button("Move")).toBeEnabled();
    flushSync(() => button("Cancel").click());
    await vi.advanceTimersByTimeAsync(250);
    expect(toggle()).toHaveFocus();
    assertNoWrites();
  });

  it("waits for the real Radix exit before retiring a cancelled mobile Move and returning focus", async () => {
    holdExitAnimation();
    render();
    const opener = await openMove();
    const dialog = moveDialog();
    flushSync(() => button("Destination").click());
    render({ width: 390 });
    expect(opener.isConnected).toBe(false);
    flushSync(() => button("Cancel").click());
    await vi.advanceTimersByTimeAsync(350);
    expect(dialog.isConnected).toBe(true);
    expect(dialog).toHaveAttribute("data-state", "closed");
    expect(toggle()).not.toHaveFocus();
    assertNoWrites();
    finishExitAnimation(dialog);
    await vi.advanceTimersByTimeAsync(1);
    expect(dialog.isConnected).toBe(false);
    expect(toggle()).toHaveFocus();
    assertNoWrites();
  });

  it("ignores an old deferred native close callback after a new opening owns the Move dialog", async () => {
    holdExitAnimation();
    render();
    await openMove();
    const previous = moveDialog();
    flushSync(() => button("Cancel").click());
    finishExitAnimation(previous);
    expect(previous.isConnected).toBe(false);
    flushSync(() => button("Reopen Move").click());
    const current = moveDialog();
    expect(current).not.toBe(previous);
    await vi.advanceTimersByTimeAsync(350);
    expect(current.isConnected).toBe(true);
    expect(current).toHaveAttribute("data-state", "open");
    expect(current.contains(document.activeElement)).toBe(true);
    flushSync(() => button("Cancel").click());
    finishExitAnimation(current);
    await vi.advanceTimersByTimeAsync(1);
    expect(button("Reopen Move")).toHaveFocus();
    assertNoWrites();
  });

  it("does not retire a newer opening admitted during the previous return-focus microtask", async () => {
    holdExitAnimation();
    render();
    const opener = await openMove();
    const previous = moveDialog();
    opener.addEventListener(
      "focus",
      () => {
        flushSync(() => button("Reopen Move").click());
      },
      { once: true },
    );
    flushSync(() => button("Cancel").click());
    finishExitAnimation(previous);
    await vi.advanceTimersByTimeAsync(350);
    const current = moveDialog();
    expect(current).not.toBe(previous);
    expect(current).toHaveAttribute("data-state", "open");
    expect(current.contains(document.activeElement)).toBe(true);
    flushSync(() => button("Cancel").click());
    finishExitAnimation(current);
    await vi.advanceTimersByTimeAsync(1);
    expect(button("Reopen Move")).toHaveFocus();
    assertNoWrites();
  });

  it.each(["session", 403, 404] as const)(
    "retires authority during an animated close without returning focus from the old %s owner",
    async (authority) => {
      holdExitAnimation();
      render();
      const opener = await openMove();
      const restoreOpener = vi.spyOn(opener, "focus");
      const restoreToggle = vi.spyOn(toggle(), "focus");
      const dialog = moveDialog();
      flushSync(() => button("Cancel").click());
      await vi.advanceTimersByTimeAsync(350);
      expect(dialog.isConnected).toBe(true);
      let outcome: unknown = null;
      try {
        if (authority === "session") {
          flushSync(() => retireMutationSession(client));
        } else {
          await client.query({
            queryKey: wikiKeys.file(ITEM.fileId),
            queryFn: () => {
              throw new ApiError(authority, "Denied");
            },
            retry: false,
          });
        }
      } catch (error) {
        outcome = error;
      }
      expect(outcome).toEqual(authority === "session" ? null : new ApiError(authority, "Denied"));
      await vi.advanceTimersByTimeAsync(1);
      expect(dialog.isConnected).toBe(false);
      expect(restoreOpener).not.toHaveBeenCalled();
      expect(restoreToggle).not.toHaveBeenCalled();
      assertNoWrites();
    },
  );

  it("returns ordinary cancellation to a surviving Actions trigger", async () => {
    render();
    const opener = await openMove();
    flushSync(() => button("Cancel").click());
    await vi.advanceTimersByTimeAsync(250);
    expect(opener).toHaveFocus();
    assertNoWrites();
  });

  it.each([
    { ...ITEM, fileId: "other-file" },
    { ...ITEM, currentName: "Renamed source" },
    { ...ITEM, driveId: "other-drive" },
    { ...ITEM, parentFolderId: "other-parent" },
  ])(
    "retires an admitted source when the same live item's verified identity changes: $fileId/$driveId/$parentFolderId",
    async (item) => {
      render();
      await openMove();
      render({ item });
      await vi.advanceTimersByTimeAsync(0);
      expect(document.querySelector('[role="dialog"]')).toBeNull();
      assertNoWrites();
    },
  );

  it("retires an old user and cannot reopen their selected destination from retained cache data", async () => {
    render();
    await openMove();
    flushSync(() => button("Destination").click());
    flushSync(() => retireMutationSession(client));
    activateMutationSession(client);
    render({ userId: "user-b" });
    await vi.advanceTimersByTimeAsync(0);
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    const file: FileDetail = {
      breadcrumb: [],
      canEdit: true,
      driveId: "source",
      driveName: "Source",
      id: ITEM.fileId,
      mimeType: MIME_DOC,
      modifiedTime: undefined,
      name: ITEM.currentName,
      parentFolderId: undefined,
    };
    flushSync(() => client.setQueryData(wikiKeys.file(ITEM.fileId), file));
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    await openMove();
    expect(button("Move")).toBeDisabled();
    assertNoWrites();
  });

  it("retires selection after a verified complete source refresh removes the file, including while the mobile tree is closed", async () => {
    render();
    await openMove();
    render({ width: 390 });
    flushSync(() =>
      client.setQueryData<DriveTree>(wikiKeys.driveTree("source"), { ...SOURCE, files: [] }),
    );
    await vi.advanceTimersByTimeAsync(0);
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    assertNoWrites();
  });

  it.each([
    { ...FILE_DETAIL, driveId: "other-drive" },
    { ...FILE_DETAIL, parentFolderId: "other-parent" },
  ])(
    "retires a closed-mobile draft when a successful native FileDetail read changes known scope: $driveId/$parentFolderId",
    async (file) => {
      render();
      await openMove();
      flushSync(() => button("Destination").click());
      render({ width: 390 });
      expect(document.querySelector("[data-source-child]")).toBeNull();
      await client.query({
        queryKey: wikiKeys.file(ITEM.fileId),
        queryFn: () => file,
      });
      await vi.advanceTimersByTimeAsync(0);
      expect(document.querySelector('[role="dialog"]')).toBeNull();
      assertNoWrites();
    },
  );

  it.each([
    { name: "unchanged", file: FILE_DETAIL, personal: false },
    {
      name: "shared root",
      file: { ...FILE_DETAIL, parentFolderId: ITEM.driveId },
      personal: false,
    },
    {
      name: "personal resolved root",
      file: { ...FILE_DETAIL, parentFolderId: ITEM.driveId },
      personal: true,
    },
    {
      name: "personal root alias",
      file: { ...FILE_DETAIL, parentFolderId: "root" },
      personal: true,
    },
    {
      name: "personal root alias without file drive",
      file: { ...FILE_DETAIL, driveId: undefined, parentFolderId: "root" },
      personal: true,
    },
    {
      name: "missing optional fields",
      file: { ...FILE_DETAIL, driveId: undefined, parentFolderId: undefined },
      personal: false,
    },
    {
      name: "missing drive with known unchanged root",
      file: { ...FILE_DETAIL, driveId: undefined, parentFolderId: ITEM.driveId },
      personal: false,
    },
  ])(
    "preserves admitted selection after a successful $name FileDetail read with a closed mobile tree",
    async ({ file, personal }) => {
      client.setQueryData(
        wikiKeys.drives,
        DRIVES.map((drive) =>
          drive.id === ITEM.driveId ? { ...drive, isPersonal: personal } : drive,
        ),
      );
      render();
      await openMove();
      flushSync(() => button("Destination").click());
      const dialog = document.querySelector('[role="dialog"]');
      render({ width: 390 });
      expect(document.querySelector("[data-source-child]")).toBeNull();
      await client.query({
        queryKey: wikiKeys.file(ITEM.fileId),
        queryFn: () => file,
      });
      await vi.advanceTimersByTimeAsync(0);
      expect(document.querySelector('[role="dialog"]')).toBe(dialog);
      expect(button("Move")).toBeEnabled();
      flushSync(() => button("Cancel").click());
      await vi.advanceTimersByTimeAsync(250);
      assertNoWrites();
    },
  );

  it.each([403, 404])(
    "retires an admitted source after a native FileDetail %s denial",
    async (status) => {
      render();
      await openMove();
      await expect(
        client.query({
          queryKey: wikiKeys.file(ITEM.fileId),
          queryFn: () => {
            throw new ApiError(status, "Denied");
          },
          retry: false,
        }),
      ).rejects.toThrow("Denied");
      await vi.advanceTimersByTimeAsync(0);
      expect(document.querySelector('[role="dialog"]')).toBeNull();
      assertNoWrites();
    },
  );

  it("retains one admitted provider write across the breakpoint and ignores cancellation while pending", async () => {
    const pending = Promise.withResolvers<ActionResult<FileResponse>>();
    ACTIONS.move.mockReturnValue(pending.promise);
    render();
    await openMove();
    flushSync(() => button("Destination").click());
    expect(button("Move")).toBeEnabled();
    flushSync(() => button("Move").click());
    await vi.advanceTimersByTimeAsync(0);
    expect(ACTIONS.move).toHaveBeenCalledExactlyOnceWith(ITEM.fileId, "destination");
    render({ width: 390 });
    await vi.advanceTimersByTimeAsync(0);
    expect(button("Cancel")).toBeDisabled();
    expect(button("Moving…")).toBeDisabled();
    flushSync(() => button("Cancel").click());
    expect(document.body).toHaveTextContent("Moving…");
    pending.resolve({
      ok: true,
      data: {
        canEdit: true,
        driveId: "destination",
        id: ITEM.fileId,
        mimeType: MIME_DOC,
        modifiedTime: undefined,
        name: ITEM.currentName,
        parentFolderId: "destination",
      },
    });
    await vi.waitFor(() => expect(document.querySelector('[role="dialog"]')).toBeNull());
  });
});

function finishExitAnimation(dialog: Element) {
  const event = new Event("animationend", { bubbles: true });
  Object.defineProperty(event, "animationName", { value: "move-exit" });
  flushSync(() => {
    dialog.dispatchEvent(event);
  });
}

function button(text: string): HTMLButtonElement {
  const result = [...document.querySelectorAll("button")].find(
    (element) => element.textContent === text,
  );
  if (!(result instanceof HTMLButtonElement)) throw new TypeError(`Missing ${text}`);
  return result;
}

function assertNoWrites() {
  expect(ACTIONS.move).not.toHaveBeenCalled();
  expect(ACTIONS.rename).not.toHaveBeenCalled();
  expect(ACTIONS.copy).not.toHaveBeenCalled();
  expect(ACTIONS.delete).not.toHaveBeenCalled();
}
