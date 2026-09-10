// @vitest-environment happy-dom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";

import { DRIVE_PREFETCH_DELAY_MS, useDrivePredictiveLoading } from "./use-drive-predictive-loading";

const API = vi.hoisted(() => ({
  getDriveFolders: vi.fn(),
  getFolderFiles: vi.fn(),
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
    API.getDriveFolders.mockResolvedValue({ filesComplete: false });
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
    expect(API.getFolderFiles).toHaveBeenCalledExactlyOnceWith("drive-1");
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
