// @vitest-environment happy-dom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";

import { ApiError } from "@/lib/api-error";
import type { DriveSearchResult } from "@/types/wiki";
import { MIME_DOC } from "@/types/wiki";

const mocks = vi.hoisted(() => ({
  getFile: vi.fn(),
  routerPrefetch: vi.fn(),
  routerPush: vi.fn(),
  searchQuery: {},
  snippetQuery: {},
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ prefetch: mocks.routerPrefetch, push: mocks.routerPush }),
}));
vi.mock("@/hooks/use-command-palette", () => ({
  useCommandPaletteState: () => ({ open: true, setOpen: vi.fn() }),
}));
vi.mock("@/hooks/use-drive-search", () => ({
  useDriveSearch: () => mocks.searchQuery,
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

describe("CommandPalette rate-limit errors", () => {
  let host: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    host = document.createElement("div");
    document.body.append(host);
    root = createRoot(host);
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

    expect(mocks.getFile).not.toHaveBeenCalled();
    expect(mocks.routerPrefetch).not.toHaveBeenCalled();

    await vi.waitFor(() => expect(mocks.getFile).toHaveBeenCalledOnce());
    expect(mocks.routerPrefetch).toHaveBeenCalledWith("/d/file-1");
  });
});

function queryState(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    data: undefined,
    error: null,
    isError: false,
    isFetching: false,
    isLoading: false,
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
