import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode, useState } from "react";
import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";
import { toast } from "sonner";

import type { ActionResult } from "@/lib/action-result";
import { retireMutationSession } from "@/lib/mutation-session";
import { wikiKeys } from "@/lib/wiki-keys";
import type { DriveNode, DriveTree, FileResponse } from "@/types/wiki";
import { MIME_DOC, MIME_FOLDER } from "@/types/wiki";

import { MoveFileDialog } from "./move-file-dialog";

const MOVE = vi.hoisted(() => vi.fn<() => Promise<ActionResult<FileResponse>>>());
vi.mock("@/app/actions/wiki-actions", () => ({ moveFileAction: MOVE }));
vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

describe("move dialog admission", () => {
  let host: HTMLDivElement;
  let root: Root;
  let client: QueryClient;
  const close = vi.fn();

  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    client = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: Infinity } },
    });
    client.setQueryData(wikiKeys.drives, [
      { id: "destination", isPersonal: false, name: "Destination" },
    ]);
    host = document.createElement("div");
    document.body.append(host);
    root = createRoot(host);
  });

  afterEach(async () => {
    flushSync(() => root.unmount());
    await vi.advanceTimersByTimeAsync(0);
    host.remove();
    client.clear();
    vi.useRealTimers();
  });

  function render() {
    flushSync(() =>
      root.render(
        <StrictMode>
          <QueryClientProvider client={client}>
            <MoveFileDialog
              currentDriveId="source"
              currentParentFolderId={undefined}
              fileId="file"
              fileName="Plan"
              onOpenChange={close}
              open
            />
          </QueryClientProvider>
        </StrictMode>,
      ),
    );
    flushSync(() => button("Destination").click());
  }

  it("admits one server move for two clicks in one React batch and retains completion after unmount", async () => {
    const pending = Promise.withResolvers<ActionResult<FileResponse>>();
    MOVE.mockReturnValue(pending.promise);
    render();
    const confirm = button("Move");
    flushSync(() => {
      confirm.click();
      confirm.click();
    });
    await vi.advanceTimersByTimeAsync(0);
    expect(MOVE).toHaveBeenCalledOnce();
    expect(button("Moving…")).toBeDisabled();
    flushSync(() => root.render(null));
    pending.resolve({ ok: true, data: movedFile() });
    await vi.advanceTimersByTimeAsync(0);
    expect(toast.success).toHaveBeenCalledOnce();
    expect(close).toHaveBeenCalledWith(false);
  });

  it("releases failed admission for retry while preserving permission guidance", async () => {
    MOVE.mockResolvedValueOnce({ ok: false, error: "Forbidden", status: 403 });
    render();
    flushSync(() => button("Move").click());
    await vi.advanceTimersByTimeAsync(0);
    expect(toast.error).toHaveBeenCalledWith(
      "Drive denied this move — you may not have edit permission on the destination.",
    );
    MOVE.mockResolvedValueOnce({ ok: true, data: movedFile() });
    flushSync(() => button("Move").click());
    await vi.advanceTimersByTimeAsync(0);
    expect(MOVE).toHaveBeenCalledTimes(2);
    expect(toast.success).toHaveBeenCalledOnce();
  });

  it("lets an admitted move settle quietly after its session retires", async () => {
    const pending = Promise.withResolvers<ActionResult<FileResponse>>();
    MOVE.mockReturnValue(pending.promise);
    render();
    flushSync(() => button("Move").click());
    await vi.advanceTimersByTimeAsync(0);
    expect(MOVE).toHaveBeenCalledOnce();
    retireMutationSession(client);
    pending.resolve({ data: movedFile(), ok: true });
    await vi.advanceTimersByTimeAsync(0);
    expect(toast.success).not.toHaveBeenCalled();
    expect(toast.error).not.toHaveBeenCalled();
    expect(close).not.toHaveBeenCalled();
  });

  it("keeps the selected destination through cancel/reopen and does not select a disabled source", async () => {
    client.setQueryData(wikiKeys.drives, [
      { id: "destination", isPersonal: false, name: "Destination" },
      { id: "source", isPersonal: false, name: "Source" },
    ]);
    flushSync(() =>
      root.render(
        <QueryClientProvider client={client}>
          <ReopenHarness />
        </QueryClientProvider>,
      ),
    );
    expect(button("Source")).toBeDisabled();
    flushSync(() => button("Source").click());
    expect(button("Move")).toBeDisabled();
    flushSync(() => button("Destination").click());
    flushSync(() => button("Cancel").click());
    await vi.advanceTimersByTimeAsync(0);
    flushSync(() => button("Show move").click());
    expect(button("Destination")).toHaveClass("bg-accent");
    expect(button("Move")).not.toBeDisabled();
    expect(MOVE).not.toHaveBeenCalled();
  });

  it("keeps a focusable destination region separate from dismissal and confirmation controls", async () => {
    let openedTarget: EventTarget | null = null;
    let closedTarget: EventTarget | null = null;
    const onOpenAutoFocus = vi.fn((event: Event) => {
      openedTarget = event.target;
    });
    const onCloseAutoFocus = vi.fn((event: Event) => {
      closedTarget = event.target;
    });
    client.setQueryData(
      wikiKeys.drives,
      Array.from({ length: 24 }, (_, index) => ({
        id: `destination-${index}`,
        isPersonal: false,
        name: `Destination ${index}`,
      })),
    );
    flushSync(() =>
      root.render(
        <QueryClientProvider client={client}>
          <ReopenHarness onCloseAutoFocus={onCloseAutoFocus} onOpenAutoFocus={onOpenAutoFocus} />
        </QueryClientProvider>,
      ),
    );
    const destinations = document.querySelector<HTMLElement>(
      '[role="region"][aria-label="Move destinations"]',
    );
    if (!destinations) throw new Error("Focusable Move destinations missing");
    const dialog = document.querySelector('[role="dialog"]');
    expect(onOpenAutoFocus).toHaveBeenCalledOnce();
    expect(openedTarget).toBe(dialog);
    destinations.focus();
    expect(document.activeElement).toBe(destinations);
    expect(destinations).toHaveTextContent("Destination 23");
    expect(button("Move")).toBeDisabled();
    flushSync(() => button("Destination 23").click());
    expect(button("Move")).not.toBeDisabled();
    expect(destinations.contains(button("Move"))).toBe(false);
    expect(destinations.contains(button("Cancel"))).toBe(false);
    expect(destinations.contains(button("Close"))).toBe(false);
    flushSync(() => button("Cancel").click());
    await vi.advanceTimersByTimeAsync(0);
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    expect(onCloseAutoFocus).toHaveBeenCalledOnce();
    expect(closedTarget).toBe(dialog);
    expect(MOVE).not.toHaveBeenCalled();
  });

  it("updates only the changed destination rows in an expanded 1,000-folder picker", async () => {
    let nameReads = 0;
    // Compiler emission can read each changed folder name four times in this StrictMode control.
    const maxNameReadsPerChangedFolder = 4;
    const accessedFolderIds = new Set<string>();
    const folders = Array.from({ length: 1_000 }, (_, index): DriveNode => ({
      id: `folder-${index}`,
      mimeType: MIME_FOLDER,
      modifiedTime: undefined,
      get name() {
        nameReads += 1;
        accessedFolderIds.add(`folder-${index}`);
        return `Folder ${index}`;
      },
      parentId: undefined,
      shortcutInaccessible: false,
      shortcutTargetDriveId: undefined,
      shortcutTargetDriveName: undefined,
      shortcutTargetId: undefined,
      shortcutTargetMimeType: undefined,
    }));
    client.setQueryData<DriveTree>(wikiKeys.driveTree("destination"), {
      driveId: "destination",
      files: [],
      filesComplete: true,
      folders,
    });
    render();
    const expand = document.querySelector('button[aria-label="Expand Destination"]');
    if (!(expand instanceof HTMLButtonElement)) throw new TypeError("Drive expansion missing");
    flushSync(() => expand.click());
    nameReads = 0;
    accessedFolderIds.clear();
    flushSync(() => button("Folder 0").click());
    expect(accessedFolderIds).toEqual(new Set(["folder-0"]));
    expect(nameReads).toBeLessThanOrEqual(maxNameReadsPerChangedFolder * accessedFolderIds.size);
    expect(button("Folder 0")).toHaveClass("bg-accent");
    expect(button("Destination")).not.toHaveClass("bg-accent");
    nameReads = 0;
    accessedFolderIds.clear();
    flushSync(() => button("Folder 999").click());
    expect(accessedFolderIds).toEqual(new Set(["folder-0", "folder-999"]));
    expect(nameReads).toBeLessThanOrEqual(maxNameReadsPerChangedFolder * accessedFolderIds.size);
    expect(button("Folder 0")).not.toHaveClass("bg-accent");
    expect(button("Folder 999")).toHaveClass("bg-accent");
    expect(button("Folder 999").type).toBe("button");
    MOVE.mockResolvedValueOnce({ ok: true, data: movedFile() });
    flushSync(() => button("Move").click());
    await vi.advanceTimersByTimeAsync(0);
    expect(MOVE).toHaveBeenCalledWith("file", "folder-999");
  });
});

function ReopenHarness({
  onCloseAutoFocus,
  onOpenAutoFocus,
}: {
  onCloseAutoFocus?: (event: Event) => void;
  onOpenAutoFocus?: (event: Event) => void;
} = {}) {
  const [open, setOpen] = useState(true);
  return (
    <>
      <button onClick={() => setOpen(true)} type="button">
        Show move
      </button>
      <MoveFileDialog
        currentDriveId="source"
        currentParentFolderId={undefined}
        fileId="file"
        fileName="Plan"
        {...(onCloseAutoFocus ? { onCloseAutoFocus } : {})}
        {...(onOpenAutoFocus ? { onOpenAutoFocus } : {})}
        onOpenChange={setOpen}
        open={open}
      />
    </>
  );
}

function button(text: string): HTMLButtonElement {
  const result = [...document.querySelectorAll("button")].find((item) => item.textContent === text);
  if (!(result instanceof HTMLButtonElement)) throw new TypeError(`Button ${text} missing`);
  return result;
}

function movedFile(): FileResponse {
  return {
    canEdit: true,
    driveId: "destination",
    id: "file",
    mimeType: MIME_DOC,
    modifiedTime: undefined,
    name: "Plan",
    parentFolderId: "destination",
  };
}
