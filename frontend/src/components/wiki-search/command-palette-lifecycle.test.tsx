import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useLayoutEffect } from "react";
import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";

import { CommandPalette } from "@/components/wiki-search/command-palette";
import { DriveSearchBox } from "@/components/wiki-sidebar/drive-search-box";
import { useCommandPaletteState } from "@/hooks/use-command-palette";
import { BrowserUserContext } from "@/lib/browser-user-context";
import { WIKI_READ_API } from "@/lib/wiki-read-api";
import {
  type DriveSearchResult,
  type FileDetail,
  MIME_DOC,
  type SnippetResult,
} from "@/types/wiki";

const ROUTER = vi.hoisted(() => ({ prefetch: vi.fn(), push: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ROUTER }));
vi.mock("@/lib/wiki-read-api", () => ({
  WIKI_READ_API: { driveSearch: vi.fn(), driveSearchSnippet: vi.fn(), getFile: vi.fn() },
}));

const RESULT_A: DriveSearchResult = {
  id: "file-a",
  name: "Previously selected A",
  mimeType: MIME_DOC,
  driveId: "drive",
  driveName: "Fixture drive",
  modifiedTime: undefined,
  parentId: undefined,
  shortcutInaccessible: false,
  shortcutTargetDriveName: undefined,
  shortcutTargetId: undefined,
  shortcutTargetMimeType: undefined,
};
const RESULT_B = { ...RESULT_A, id: "file-b", name: "Current selected B" };
const FILE_B: FileDetail = {
  id: RESULT_B.id,
  name: RESULT_B.name,
  mimeType: MIME_DOC,
  driveId: undefined,
  driveName: undefined,
  modifiedTime: undefined,
  parentFolderId: undefined,
  breadcrumb: [],
  canEdit: false,
};
const snippet = (fileId: string, text: string): SnippetResult => ({
  fileId,
  mimeType: MIME_DOC,
  matchTerm: undefined,
  snippet: text,
});
let control: ReturnType<typeof useCommandPaletteState>;

function Controls() {
  const state = useCommandPaletteState();
  useLayoutEffect(() => {
    control = state;
  }, [state]);
  return (
    <>
      <DriveSearchBox />
      <CommandPalette />
    </>
  );
}

describe("current-query search and native focus ownership", () => {
  let host: HTMLDivElement;
  let root: Root;
  let client: QueryClient;

  beforeEach(() => {
    vi.useFakeTimers();
    vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
    vi.mocked(WIKI_READ_API.driveSearch).mockReset().mockResolvedValue([]);
    vi.mocked(WIKI_READ_API.driveSearchSnippet)
      .mockReset()
      .mockResolvedValue(snippet(RESULT_B.id, "Current B preview"));
    vi.mocked(WIKI_READ_API.getFile).mockReset().mockResolvedValue(FILE_B);
    ROUTER.prefetch.mockReset();
    ROUTER.push.mockReset();
    client = new QueryClient({ defaultOptions: { queries: { gcTime: Infinity, retry: false } } });
    host = document.createElement("div");
    document.body.append(host);
    root = createRoot(host);
  });

  afterEach(async () => {
    flushSync(() => control.setOpen(false));
    flushSync(() => root.unmount());
    client.clear();
    await vi.advanceTimersByTimeAsync(0);
    host.remove();
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  function mountPalette() {
    if (host.childElementCount > 0) return;
    flushSync(() =>
      root.render(
        <QueryClientProvider client={client}>
          <BrowserUserContext value="user-a">
            <Controls />
          </BrowserUserContext>
        </QueryClientProvider>,
      ),
    );
    flushSync(() => control.setOpen(false));
  }

  function opener(): HTMLButtonElement {
    const element = host.querySelector('[aria-label="Open search"]');
    if (!(element instanceof HTMLButtonElement)) throw new TypeError("Search opener missing");
    return element;
  }

  async function openSearch() {
    mountPalette();
    flushSync(() => {
      opener().focus();
      opener().click();
    });
    await vi.advanceTimersByTimeAsync(20);
    expect(input()).toHaveFocus();
  }

  it("shows Searching during B admission and its pending read, then uses only settled B authority", async () => {
    const first = Promise.withResolvers<DriveSearchResult[]>();
    const second = Promise.withResolvers<DriveSearchResult[]>();
    vi.mocked(WIKI_READ_API.driveSearch)
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise);
    await openSearch();
    type("alpha");
    await vi.advanceTimersByTimeAsync(250);
    await vi.advanceTimersByTimeAsync(0);
    expect(WIKI_READ_API.driveSearch).toHaveBeenCalledOnce();
    first.resolve([]);
    await vi.advanceTimersByTimeAsync(0);
    expect(document.body).toHaveTextContent("No results");
    type("bravo");
    expect(document.body).toHaveTextContent("Searching…");
    expect(document.body).not.toHaveTextContent("No results");
    await vi.advanceTimersByTimeAsync(249);
    expect(WIKI_READ_API.driveSearch).toHaveBeenCalledOnce();
    await vi.advanceTimersByTimeAsync(1);
    await vi.waitFor(() => expect(WIKI_READ_API.driveSearch).toHaveBeenCalledTimes(2));
    expect(document.body).toHaveTextContent("Searching…");
    expect(document.body).not.toHaveTextContent("No results");
    second.resolve([RESULT_B]);
    await vi.advanceTimersByTimeAsync(0);
    expect(document.body).toHaveTextContent(RESULT_B.name);
    expect(document.body).not.toHaveTextContent("No results");
    await vi.advanceTimersByTimeAsync(150);
    expect(WIKI_READ_API.getFile).toHaveBeenCalledExactlyOnceWith(
      RESULT_B.id,
      expect.any(AbortSignal),
    );
    expect(WIKI_READ_API.driveSearchSnippet).toHaveBeenCalledOnce();
    expect(vi.mocked(WIKI_READ_API.driveSearch).mock.calls.map(([query]) => query)).toEqual([
      "alpha",
      "bravo",
    ]);
  });

  it("hides A rows and a late A snippet while typing B and cancels obsolete A speculation", async () => {
    const pendingA = Promise.withResolvers<SnippetResult>();
    const pendingB = Promise.withResolvers<DriveSearchResult[]>();
    vi.mocked(WIKI_READ_API.driveSearch)
      .mockResolvedValueOnce([RESULT_A])
      .mockReturnValueOnce(pendingB.promise);
    vi.mocked(WIKI_READ_API.driveSearchSnippet)
      .mockReturnValueOnce(pendingA.promise)
      .mockResolvedValueOnce(snippet(RESULT_B.id, "Current B preview"));
    await openSearch();
    type("alpha");
    await vi.advanceTimersByTimeAsync(250);
    await vi.advanceTimersByTimeAsync(0);
    await vi.advanceTimersByTimeAsync(0);
    expect(document.body).toHaveTextContent(RESULT_A.name);
    expect(WIKI_READ_API.driveSearchSnippet).toHaveBeenCalledOnce();
    type("bravo");
    expect(document.body).not.toHaveTextContent(RESULT_A.name);
    expect(document.body).toHaveTextContent("Searching…");
    pendingA.resolve(snippet(RESULT_A.id, "Retired A preview must stay hidden"));
    await vi.advanceTimersByTimeAsync(150);
    expect(document.body).not.toHaveTextContent("Retired A preview");
    expect(WIKI_READ_API.getFile).not.toHaveBeenCalled();
    expect(WIKI_READ_API.driveSearch).toHaveBeenCalledOnce();
    await vi.advanceTimersByTimeAsync(100);
    expect(WIKI_READ_API.driveSearch).toHaveBeenCalledTimes(2);
    pendingB.resolve([RESULT_B]);
    await vi.advanceTimersByTimeAsync(0);
    await vi.advanceTimersByTimeAsync(150);
    expect(document.body).toHaveTextContent(RESULT_B.name);
    expect(document.body).toHaveTextContent("Current B preview");
    expect(document.body).not.toHaveTextContent("Retired A preview");
    expect(WIKI_READ_API.getFile).toHaveBeenCalledExactlyOnceWith(
      RESULT_B.id,
      expect.any(AbortSignal),
    );
    expect(WIKI_READ_API.driveSearchSnippet).toHaveBeenCalledTimes(2);
  });

  it("returns Clear focus to the combobox and does not read an empty term", async () => {
    await openSearch();
    type("alpha");
    await vi.advanceTimersByTimeAsync(250);
    await vi.advanceTimersByTimeAsync(0);
    const clear = document.querySelector('[aria-label="Clear search"]');
    if (!(clear instanceof HTMLButtonElement)) throw new TypeError("Clear control missing");
    flushSync(() => {
      clear.focus();
      clear.click();
    });
    expect(input()).toHaveValue("");
    expect(input()).toHaveFocus();
    await vi.advanceTimersByTimeAsync(250);
    await vi.advanceTimersByTimeAsync(0);
    expect(input()).toHaveFocus();
    expect(WIKI_READ_API.driveSearch).toHaveBeenCalledOnce();
  });

  it("does not revive selectable rows, preview or speculation when a held old query finishes after Clear", async () => {
    const pending = Promise.withResolvers<DriveSearchResult[]>();
    vi.mocked(WIKI_READ_API.driveSearch).mockReturnValueOnce(pending.promise);
    await openSearch();
    type("alpha");
    await vi.advanceTimersByTimeAsync(250);
    await vi.advanceTimersByTimeAsync(0);
    expect(WIKI_READ_API.driveSearch).toHaveBeenCalledOnce();
    const clear = document.querySelector('[aria-label="Clear search"]');
    if (!(clear instanceof HTMLButtonElement)) throw new TypeError("Clear control missing");
    flushSync(() => {
      clear.focus();
      clear.click();
    });
    pending.resolve([RESULT_A]);
    await vi.advanceTimersByTimeAsync(0);
    expect(input()).toHaveValue("");
    expect(input()).toHaveFocus();
    expect(document.querySelectorAll("[cmdk-item]")).toHaveLength(0);
    expect(document.body).not.toHaveTextContent(RESULT_A.name);
    expect(document.body).toHaveTextContent("Type at least 2 characters to search");
    await vi.advanceTimersByTimeAsync(250);
    await vi.advanceTimersByTimeAsync(0);
    expect(document.querySelectorAll("[cmdk-item]")).toHaveLength(0);
    expect(WIKI_READ_API.driveSearch).toHaveBeenCalledOnce();
    expect(WIKI_READ_API.driveSearchSnippet).not.toHaveBeenCalled();
    expect(WIKI_READ_API.getFile).not.toHaveBeenCalled();
    type("alpha");
    await vi.advanceTimersByTimeAsync(250);
    await vi.advanceTimersByTimeAsync(0);
    expect(document.body).toHaveTextContent(RESULT_A.name);
    expect(document.querySelectorAll("[cmdk-item]")).toHaveLength(1);
    expect(WIKI_READ_API.driveSearch).toHaveBeenCalledOnce();
  });

  it("returns native Escape focus to the pointer opener and supports a fresh reopen", async () => {
    await openSearch();
    flushSync(() =>
      input().dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, key: "Escape" })),
    );
    await vi.advanceTimersByTimeAsync(0);
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    expect(opener()).toHaveFocus();
    await openSearch();
    expect(input()).toHaveValue("");
    expect(input()).toHaveFocus();
    expect(WIKI_READ_API.driveSearch).not.toHaveBeenCalled();
  });

  it("offers a separate Close control that dismisses search and returns focus to its opener", async () => {
    await openSearch();
    type("alpha");
    const close = document.querySelector<HTMLButtonElement>('[aria-label="Close search"]');
    if (!close) throw new Error("Search dismissal control missing");
    expect(document.querySelector('[aria-label="Clear search"]')).not.toBeNull();
    flushSync(() => {
      close.focus();
      close.click();
    });
    await vi.advanceTimersByTimeAsync(0);
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    expect(opener()).toHaveFocus();
    await openSearch();
    expect(input()).toHaveValue("");
    expect(WIKI_READ_API.driveSearch).not.toHaveBeenCalled();
  });

  it("cancels opening RAF work when the palette closes before autofocus", async () => {
    mountPalette();
    flushSync(() => {
      opener().focus();
      opener().click();
    });
    flushSync(() => control.setOpen(false));
    await vi.advanceTimersByTimeAsync(32);
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    expect(opener()).toHaveFocus();
    await openSearch();
    expect(input()).toHaveFocus();
  });
});

function input(): HTMLInputElement {
  const element = document.querySelector('[aria-label="Search Drive"]');
  if (!(element instanceof HTMLInputElement)) throw new TypeError("Search field missing");
  return element;
}

function type(value: string) {
  const element = input();
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set?.bind(
    element,
  );
  if (!setter) throw new Error("Native input setter missing");
  flushSync(() => {
    setter(value);
    element.dispatchEvent(new Event("input", { bubbles: true }));
  });
}
