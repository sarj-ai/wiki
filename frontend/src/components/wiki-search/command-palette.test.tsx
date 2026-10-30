// @vitest-environment happy-dom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";

import { ApiError } from "@/lib/api-error";
import type { DriveSearchResult } from "@/types/wiki";
import { MIME_DOC } from "@/types/wiki";

const mocks = vi.hoisted(() => ({
  getFile: vi.fn(),
  open: true,
  routerPrefetch: vi.fn(),
  routerPush: vi.fn(),
  returnFocusRef: { current: null },
  searchQuery: {},
  searchEnabled: vi.fn(),
  snippetQuery: {},
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ prefetch: mocks.routerPrefetch, push: mocks.routerPush }),
}));
vi.mock("@/hooks/use-command-palette", () => ({
  useCommandPaletteState: () => ({
    open: mocks.open,
    returnFocusRef: mocks.returnFocusRef,
    setOpen: vi.fn(),
  }),
}));
vi.mock("@/hooks/use-drive-search", () => ({
  useDriveSearchState: (_query: string, enabled: boolean) => {
    mocks.searchEnabled(enabled);
    return { query: mocks.searchQuery, isCurrentQuery: true };
  },
  useDriveSearchSnippet: () => mocks.snippetQuery,
}));
vi.mock("@/lib/browser-user-context", () => ({ useBrowserUserId: () => "user-1" }));
vi.mock("@/lib/wiki-read-api", () => ({ WIKI_READ_API: { getFile: mocks.getFile } }));

import { CommandPalette } from "./command-palette";

const RESULT: DriveSearchResult = {
  driveId: "drive-1",
  driveName: "Product",
  id: "file-1",
  mimeType: MIME_DOC,
  modifiedTime: undefined,
  name: "Roadmap",
  parentId: undefined,
  shortcutInaccessible: false,
  shortcutTargetDriveName: undefined,
  shortcutTargetId: undefined,
  shortcutTargetMimeType: undefined,
};
const ORIGINAL_CONNECTION = Object.getOwnPropertyDescriptor(navigator, "connection");

describe("CommandPalette rate-limit errors", () => {
  let host: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    host = document.createElement("div");
    document.body.append(host);
    root = createRoot(host);
    mocks.open = true;
    mocks.searchEnabled.mockClear();
    mocks.searchQuery = queryState();
    mocks.snippetQuery = queryState();
    mocks.getFile.mockReset();
    mocks.getFile.mockResolvedValue(RESULT);
    mocks.routerPrefetch.mockReset();
    mocks.routerPush.mockReset();
  });

  afterEach(() => {
    flushSync(() => root.unmount());
    host.remove();
    vi.restoreAllMocks();
    if (ORIGINAL_CONNECTION) Object.defineProperty(navigator, "connection", ORIGINAL_CONNECTION);
    else Reflect.deleteProperty(navigator, "connection");
  });

  it("renders a retryable search rate limit instead of No results", async () => {
    const refetch = vi.fn();
    mocks.searchQuery = queryState({
      error: new ApiError(429, "upstream body"),
      isError: true,
      refetch,
    });
    renderPalette(root);
    await enterSearchQuery();

    expect(document.body).toHaveTextContent("Too many requests. Try again in a minute.");
    expect(document.body).not.toHaveTextContent("No results");

    clickButton("Retry search");
    expect(refetch).toHaveBeenCalledOnce();
  });

  it("disables search retry while the refetch is active", async () => {
    mocks.searchQuery = queryState({
      error: new ApiError(429, "upstream body"),
      isError: true,
      isFetching: true,
    });
    renderPalette(root);
    await enterSearchQuery();

    expect(findButton("Retry search")).toBeDisabled();
  });

  it("renders a retryable snippet rate limit instead of no-match copy", async () => {
    const refetch = vi.fn();
    mocks.searchQuery = queryState({ data: [RESULT] });
    mocks.snippetQuery = queryState({
      error: new ApiError(429, "upstream body"),
      isError: true,
      refetch,
    });
    renderPalette(root);
    await enterSearchQuery();

    expect(document.body).toHaveTextContent("Too many requests. Try again in a minute.");
    expect(document.body).not.toHaveTextContent("couldn't pinpoint the exact phrase");

    clickButton("Retry preview");
    expect(refetch).toHaveBeenCalledOnce();
  });

  it("prepares only the stable selected document after the intent delay", async () => {
    mocks.searchQuery = queryState({ data: [RESULT] });
    renderPalette(root);

    await enterSearchQuery();

    expect(mocks.getFile).not.toHaveBeenCalled();
    expect(mocks.routerPrefetch).not.toHaveBeenCalled();

    await vi.waitFor(() => expect(mocks.getFile).toHaveBeenCalledOnce());
    expect(mocks.routerPrefetch).toHaveBeenCalledWith("/d/file-1");
  });

  it("disables the retained search observer when keyboard dismissal closes the palette", () => {
    renderPalette(root);
    expect(mocks.searchEnabled).toHaveBeenLastCalledWith(true);
    mocks.open = false;
    renderPalette(root);
    expect(mocks.searchEnabled).toHaveBeenLastCalledWith(false);
  });

  it.each(["hidden", "saveData"])(
    "skips %s speculation while explicit opening still prepares the document",
    async (condition) => {
      mocks.searchQuery = queryState({ data: [RESULT] });
      vi.spyOn(document, "visibilityState", "get").mockReturnValue(
        condition === "hidden" ? "hidden" : "visible",
      );
      Object.defineProperty(navigator, "connection", {
        configurable: true,
        value: { saveData: condition === "saveData" },
      });
      renderPalette(root);
      await enterSearchQuery();
      await new Promise((resolve) => {
        window.setTimeout(resolve, 200);
      });
      expect(mocks.getFile).not.toHaveBeenCalled();
      expect(mocks.routerPrefetch).not.toHaveBeenCalled();
      const input = document.body.querySelector<HTMLInputElement>('[aria-label="Search Drive"]');
      if (!input) throw new Error("Search input not found");
      flushSync(() =>
        input.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, key: "Enter" })),
      );
      await vi.waitFor(() => expect(mocks.getFile).toHaveBeenCalledOnce());
      expect(mocks.routerPush).toHaveBeenCalledWith("/d/file-1");
    },
  );
});

function queryState(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    data: undefined,
    error: null,
    isError: false,
    isFetching: false,
    isLoading: false,
    isPending: false,
    isSuccess: overrides["isError"] !== true,
    refetch: vi.fn(),
    ...overrides,
  };
}

function renderPalette(root: Root): void {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  flushSync(() => {
    root.render(
      <QueryClientProvider client={queryClient}>
        <CommandPalette />
      </QueryClientProvider>,
    );
  });
}

async function enterSearchQuery(): Promise<void> {
  const input = document.body.querySelector<HTMLInputElement>('[aria-label="Search Drive"]');
  if (input === null) throw new Error("Search input not found");
  flushSync(() => {
    const setValue = Object.getOwnPropertyDescriptor(
      HTMLInputElement.prototype,
      "value",
    )?.set?.bind(input);
    if (setValue === undefined) throw new Error("Input value setter not found");
    setValue("roadmap");
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await vi.waitFor(() => expect(input).toHaveValue("roadmap"));
}

function clickButton(text: string): void {
  const button = findButton(text);
  flushSync(() => button.click());
}

function findButton(text: string): HTMLButtonElement {
  const button = [...document.body.querySelectorAll<HTMLButtonElement>("button")].find(
    (candidate) => candidate.textContent === text,
  );
  if (button === undefined) throw new Error(`${text} button not found`);
  return button;
}
