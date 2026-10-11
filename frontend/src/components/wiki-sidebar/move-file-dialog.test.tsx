import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";
import { toast } from "sonner";

import type { ActionResult } from "@/lib/action-result";
import { retireMutationSession } from "@/lib/mutation-session";
import { wikiKeys } from "@/lib/wiki-keys";
import type { FileResponse } from "@/types/wiki";
import { MIME_DOC } from "@/types/wiki";

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
});

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
