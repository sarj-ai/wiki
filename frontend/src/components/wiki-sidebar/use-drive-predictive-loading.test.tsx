// @vitest-environment happy-dom
import { CancelledError, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";

import type { WIKI_READ_API } from "@/lib/wiki-read-api";
import type { DriveTree } from "@/types/wiki";

import { DRIVE_PREFETCH_DELAY_MS, useDrivePredictiveLoading } from "./use-drive-predictive-loading";

type WikiReadApi = typeof WIKI_READ_API;

const API = vi.hoisted(() => ({
  getDriveFolders: vi.fn<WikiReadApi["getDriveFolders"]>(),
  getFolderFiles: vi.fn<WikiReadApi["getFolderFiles"]>(),
}));

vi.mock("@/lib/wiki-read-api", () => ({
  WIKI_READ_API: API,
}));

describe("useDrivePredictiveLoading", () => {
  let button: HTMLButtonElement;
  let container: HTMLDivElement;
  let queryClient: QueryClient;
  let root: Root;

  beforeEach(() => {
    vi.useFakeTimers();
    Object.defineProperty(navigator, "connection", { configurable: true, value: undefined });
    API.getDriveFolders.mockReset();
    API.getFolderFiles.mockReset();
    API.getDriveFolders.mockResolvedValue({
      driveId: "drive-1",
      files: [],
      folders: [],
      filesComplete: false,
    });
    API.getFolderFiles.mockResolvedValue([]);
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
  });

  afterEach(() => {
    flushSync(() => root.unmount());
    container.remove();
    queryClient.clear();
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("prefetches a collapsed drive and incomplete root after 150ms mouse intent", async () => {
    mount();
    pointer("pointerover", "mouse");

    await advance(DRIVE_PREFETCH_DELAY_MS - 1);
    expect(API.getDriveFolders).not.toHaveBeenCalled();
    await advance(1);

    expect(API.getDriveFolders).toHaveBeenCalledOnce();
    expect(API.getFolderFiles).toHaveBeenCalledOnce();
    const [folderId, signal] = API.getFolderFiles.mock.calls[0] ?? [];
    expect(folderId).toBe("drive-1");
    expect(signal).toBeInstanceOf(AbortSignal);
  });

  it("keeps the timer while either hover or focus still owns the intent", async () => {
    mount();
    pointer("pointerover", "pen");
    button.focus();
    await advance(100);
    pointer("pointerout", "pen");
    await advance(50);

    expect(API.getDriveFolders).toHaveBeenCalledOnce();
  });

  it("shares one preparation continuation across four overlapping primary activations", async () => {
    const pending = Promise.withResolvers<DriveTree>();
    API.getDriveFolders.mockReturnValue(pending.promise);
    const prepare = vi.spyOn(queryClient, "query");
    mount();
    for (let index = 0; index < 4; index += 1) pointer("pointerdown", "mouse");
    await advance(0);
    expect(API.getDriveFolders).toHaveBeenCalledOnce();
    pending.resolve({ driveId: "drive-1", files: [], folders: [], filesComplete: false });
    await advance(0);
    expect(API.getFolderFiles).toHaveBeenCalledOnce();
    expect(
      prepare.mock.calls.filter(([options]) => options.queryKey[1] === "drive-tree"),
    ).toHaveLength(1);
    expect(
      prepare.mock.calls.filter(([options]) => options.queryKey[1] === "folder-files"),
    ).toHaveLength(1);
  });

  it("upgrades pending hover to explicit intent and retries after a failed preparation", async () => {
    const pending = Promise.withResolvers<DriveTree>();
    API.getDriveFolders.mockReturnValueOnce(pending.promise);
    mount();
    pointer("pointerover", "mouse");
    await advance(DRIVE_PREFETCH_DELAY_MS);
    pointer("pointerdown", "mouse");
    pointer("pointerout", "mouse");
    pending.resolve({ driveId: "drive-1", files: [], folders: [], filesComplete: false });
    await advance(0);
    expect(API.getFolderFiles).toHaveBeenCalledOnce();

    queryClient.clear();
    API.getDriveFolders.mockRejectedValueOnce(new Error("temporarily unavailable"));
    pointer("pointerdown", "mouse");
    await advance(0);
    pointer("pointerdown", "mouse");
    await advance(0);
    expect(API.getDriveFolders).toHaveBeenCalledTimes(3);
    expect(API.getFolderFiles).toHaveBeenCalledTimes(2);
  });

  it.each(["query", "abort"] as const)("keeps canceled %s preparations quiet", async (kind) => {
    const warn = vi.spyOn(console, "warn");
    const error =
      kind === "query"
        ? new CancelledError()
        : new DOMException("The hover read was retired", "AbortError");
    API.getDriveFolders.mockRejectedValueOnce(error);
    mount();
    pointer("pointerdown", "mouse");
    await advance(0);

    expect(API.getDriveFolders).toHaveBeenCalledOnce();
    expect(API.getFolderFiles).not.toHaveBeenCalled();
    expect(warn).not.toHaveBeenCalled();
  });

  it("cancels speculative work after the last intent leaves", async () => {
    mount();
    pointer("pointerover", "mouse");
    button.focus();
    pointer("pointerout", "mouse");
    button.blur();
    await advance(DRIVE_PREFETCH_DELAY_MS);

    expect(API.getDriveFolders).not.toHaveBeenCalled();
  });

  it("ignores touch hover but starts immediately on primary touch pointerdown", async () => {
    mount();
    pointer("pointerover", "touch");
    await advance(DRIVE_PREFETCH_DELAY_MS);
    expect(API.getDriveFolders).not.toHaveBeenCalled();

    pointer("pointerdown", "touch");
    await settleQueries();

    expect(API.getDriveFolders).toHaveBeenCalledOnce();
  });

  it("does not prepare expanded drives", async () => {
    mount(true);
    pointer("pointerover", "mouse");
    pointer("pointerdown", "mouse");
    await advance(DRIVE_PREFETCH_DELAY_MS);

    expect(API.getDriveFolders).not.toHaveBeenCalled();
  });

  it("does not prepare a collapsed drive for modified pointer activation", async () => {
    mount();
    pointer("pointerdown", "mouse", { ctrlKey: true });
    await settleQueries();

    expect(API.getDriveFolders).not.toHaveBeenCalled();
  });

  it.each(["leave", "hidden", "expanded", "unmounted"])(
    "does not start a second speculative read after intent becomes %s",
    async (mode) => {
      const pending = Promise.withResolvers<DriveTree>();
      API.getDriveFolders.mockReturnValue(pending.promise);
      mount();
      pointer("pointerover", "mouse");
      await advance(DRIVE_PREFETCH_DELAY_MS);
      expect(API.getDriveFolders).toHaveBeenCalledOnce();
      const [, signal] = API.getDriveFolders.mock.calls[0] ?? [];
      switch (mode) {
        case "leave": {
          pointer("pointerout", "mouse");
          break;
        }
        case "hidden": {
          vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
          break;
        }
        case "expanded": {
          mount(true);
          break;
        }
        case "unmounted": {
          flushSync(() => root.render(null));
          break;
        }
        default: {
          throw new Error("Unknown intent transition");
        }
      }
      pending.resolve({ driveId: "drive-1", files: [], folders: [], filesComplete: false });
      await advance(0);
      expect(API.getFolderFiles).not.toHaveBeenCalled();
      expect(signal?.aborted).toBe(false);
    },
  );

  it("rechecks page visibility when a speculative timer fires", async () => {
    mount();
    pointer("pointerover", "mouse");
    vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
    await advance(DRIVE_PREFETCH_DELAY_MS);
    expect(API.getDriveFolders).not.toHaveBeenCalled();
  });

  it.each(["save-data", "hidden"])("does not speculate when the page is %s", async (mode) => {
    if (mode === "save-data") {
      Object.defineProperty(navigator, "connection", {
        configurable: true,
        value: { saveData: true },
      });
    } else {
      vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
    }
    mount();
    pointer("pointerover", "mouse");
    await advance(DRIVE_PREFETCH_DELAY_MS);

    expect(API.getDriveFolders).not.toHaveBeenCalled();
  });

  function pointer(type: string, pointerType: string, init: PointerEventInit = {}): void {
    button.dispatchEvent(
      new PointerEvent(type, { bubbles: true, button: 0, pointerType, ...init }),
    );
  }

  function mount(expanded = false): void {
    renderHarness(root, queryClient, expanded);
    const rendered = container.querySelector("button");
    if (!(rendered instanceof HTMLButtonElement)) throw new TypeError("button missing");
    button = rendered;
  }
});

function Harness({ expanded }: { expanded: boolean }) {
  const handlers = useDrivePredictiveLoading("drive-1", expanded);
  return (
    <button type="button" {...handlers}>
      Drive
    </button>
  );
}

function renderHarness(root: Root, queryClient: QueryClient, expanded: boolean): void {
  flushSync(() => {
    root.render(
      <QueryClientProvider client={queryClient}>
        <Harness expanded={expanded} />
      </QueryClientProvider>,
    );
  });
}

async function settleQueries(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
}

async function advance(ms: number): Promise<void> {
  await vi.advanceTimersByTimeAsync(ms);
  await settleQueries();
}
