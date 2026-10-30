import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useLayoutEffect } from "react";
import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";

import { SidebarProvider, useSidebar } from "@/components/ui/sidebar";
import { DriveSearchBox } from "@/components/wiki-sidebar/drive-search-box";
import { SidebarNavigationProvider } from "@/components/wiki-sidebar/use-sidebar-navigation";
import { useCommandPaletteHotkey, useCommandPaletteState } from "@/hooks/use-command-palette";
import { BrowserUserContext } from "@/lib/browser-user-context";
import { WIKI_READ_API } from "@/lib/wiki-read-api";
import { type DriveSearchResult, MIME_DOC, MIME_FOLDER, MIME_FORM } from "@/types/wiki";

import { CommandPalette } from "./command-palette";

const ROUTER = vi.hoisted(() => ({ prefetch: vi.fn(), push: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ROUTER }));
vi.mock("@/lib/wiki-read-api", () => ({
  WIKI_READ_API: { driveSearch: vi.fn(), driveSearchSnippet: vi.fn(), getFile: vi.fn() },
}));

const RESULT: DriveSearchResult = {
  id: "selected-doc",
  name: "Selected document",
  mimeType: MIME_DOC,
  driveId: "fixture-drive",
  driveName: "Fixture drive",
  modifiedTime: undefined,
  parentId: undefined,
  shortcutInaccessible: false,
  shortcutTargetDriveName: undefined,
  shortcutTargetId: undefined,
  shortcutTargetMimeType: undefined,
};
let control: ReturnType<typeof useCommandPaletteState>;

function Controls({
  pathname,
  showOpener,
  userId,
}: {
  pathname: string;
  showOpener: boolean;
  userId: string;
}) {
  useCommandPaletteHotkey();
  const state = useCommandPaletteState();
  const { openMobile, setOpenMobile } = useSidebar();
  useLayoutEffect(() => {
    control = state;
  }, [state]);
  return (
    <>
      <button onClick={() => setOpenMobile(true)} type="button">
        Open drawer
      </button>
      <output>{openMobile ? "Drawer open" : "Drawer closed"}</output>
      {showOpener ? (
        <SidebarNavigationProvider ownerKey={userId} pathname={pathname}>
          <DriveSearchBox />
        </SidebarNavigationProvider>
      ) : null}
      {/* The retained palette lives outside the responsive sidebar's navigation context. */}
      <CommandPalette />
    </>
  );
}

describe("document selection from a mobile sidebar search opening", () => {
  let host: HTMLDivElement;
  let root: Root;
  let client: QueryClient;
  let initialWidth: number;

  beforeEach(() => {
    vi.useFakeTimers();
    initialWidth = window.innerWidth;
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 390 });
    vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
    vi.mocked(WIKI_READ_API.driveSearch).mockReset().mockResolvedValue([RESULT]);
    vi.mocked(WIKI_READ_API.driveSearchSnippet).mockReset().mockResolvedValue({
      fileId: RESULT.id,
      mimeType: MIME_DOC,
      matchTerm: undefined,
      snippet: "Selected preview",
    });
    vi.mocked(WIKI_READ_API.getFile).mockReset().mockResolvedValue({
      id: RESULT.id,
      name: RESULT.name,
      mimeType: MIME_DOC,
      driveId: undefined,
      driveName: undefined,
      modifiedTime: undefined,
      parentFolderId: undefined,
      breadcrumb: [],
      canEdit: false,
    });
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
    Object.defineProperty(window, "innerWidth", { configurable: true, value: initialWidth });
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  function render({ pathname = "/d/current", showOpener = true, userId = "user-a" } = {}) {
    flushSync(() =>
      root.render(
        <QueryClientProvider client={client}>
          <BrowserUserContext value={userId}>
            <SidebarProvider>
              <Controls pathname={pathname} showOpener={showOpener} userId={userId} />
            </SidebarProvider>
          </BrowserUserContext>
        </QueryClientProvider>,
      ),
    );
  }

  async function openSearch() {
    flushSync(() => button("Open drawer").click());
    const opener = host.querySelector('[aria-label="Open search"]');
    if (!(opener instanceof HTMLButtonElement)) throw new TypeError("Search opener missing");
    flushSync(() => {
      opener.focus();
      opener.click();
    });
    await vi.advanceTimersByTimeAsync(20);
    expect(input()).toHaveFocus();
    expect(control.onDocumentNavigate).toBeTypeOf("function");
    typeQuery("selected");
    await vi.advanceTimersByTimeAsync(250);
    await vi.advanceTimersByTimeAsync(0);
    expect(document.body).toHaveTextContent(RESULT.name);
  }

  it("captures the current mobile owner through the real sibling global Mod+K hook and waits for commit", async () => {
    render();
    await openHotkeySearch();
    selectDocument();
    expect(ROUTER.push).toHaveBeenCalledExactlyOnceWith("/d/selected-doc");
    expect(control.onDocumentNavigate).toBeNull();
    expect(host).toHaveTextContent("Drawer open");
    render({ pathname: "/d/selected-doc" });
    await vi.waitFor(() => expect(host).toHaveTextContent("Drawer closed"));
    expect(WIKI_READ_API.driveSearch).toHaveBeenCalledOnce();
  });

  it("retires a global shortcut opening across user change and rejects its held callback", async () => {
    render();
    await openHotkeySearch();
    const oldNavigate = control.onDocumentNavigate;
    if (!oldNavigate) throw new Error("Global navigation capture missing");
    render({ userId: "user-b" });
    expect(control.onDocumentNavigate).toBeNull();
    flushSync(() => oldNavigate("/d/selected-doc"));
    render({ userId: "user-b", pathname: "/d/selected-doc" });
    expect(host).toHaveTextContent("Drawer open");
    flushSync(() => control.setOpen(false));
    await openHotkeySearch();
    selectDocument();
    expect(host).toHaveTextContent("Drawer closed");
  });

  it("retires a global opening when its sidebar provider unmounts and does not reuse the registration", async () => {
    render();
    await openHotkeySearch();
    const oldNavigate = control.onDocumentNavigate;
    if (!oldNavigate) throw new Error("Global navigation capture missing");
    render({ showOpener: false });
    expect(control.onDocumentNavigate).toBeNull();
    flushSync(() => oldNavigate("/d/selected-doc"));
    render({ showOpener: false, pathname: "/d/selected-doc" });
    expect(host).toHaveTextContent("Drawer open");
    flushSync(() => control.setOpen(false));
    dispatchHotkey();
    expect(control.open).toBe(true);
    expect(control.onDocumentNavigate).toBeNull();
  });

  it("rejects a previous global opening callback after a new shortcut opening under the same owner", async () => {
    render();
    await openHotkeySearch();
    const oldNavigate = control.onDocumentNavigate;
    if (!oldNavigate) throw new Error("Global navigation capture missing");
    flushSync(() => control.setOpen(false));
    await openHotkeySearch();
    flushSync(() => oldNavigate("/d/selected-doc"));
    render({ pathname: "/d/selected-doc" });
    expect(host).toHaveTextContent("Drawer open");
    selectDocument();
    expect(host).toHaveTextContent("Drawer closed");
  });

  it("keeps pending navigation open, clears the palette callback, and closes only at the matching commit", async () => {
    render();
    await openSearch();
    selectDocument();
    expect(ROUTER.push).toHaveBeenCalledExactlyOnceWith("/d/selected-doc");
    expect(control.open).toBe(false);
    expect(control.onDocumentNavigate).toBeNull();
    expect(host).toHaveTextContent("Drawer open");
    render();
    expect(host).toHaveTextContent("Drawer open");
    render({ pathname: "/d/selected-doc" });
    await vi.waitFor(() => expect(host).toHaveTextContent("Drawer closed"));
    expect(WIKI_READ_API.driveSearch).toHaveBeenCalledOnce();
    expect(WIKI_READ_API.getFile).toHaveBeenCalledOnce();
  });

  it("closes immediately when the selected document is already the current route", async () => {
    render({ pathname: "/d/selected-doc" });
    await openSearch();
    selectDocument();
    expect(ROUTER.push).toHaveBeenCalledExactlyOnceWith("/d/selected-doc");
    expect(host).toHaveTextContent("Drawer closed");
  });

  it("does not close for an accepted request that never commits or commits to another route", async () => {
    render();
    await openSearch();
    selectDocument();
    render();
    expect(host).toHaveTextContent("Drawer open");
    render({ pathname: "/d/other" });
    render({ pathname: "/d/selected-doc" });
    expect(host).toHaveTextContent("Drawer open");
  });

  it("retires an accepted pending destination when the verified sidebar owner changes", async () => {
    render();
    await openSearch();
    selectDocument();
    render({ userId: "user-b" });
    render({ userId: "user-b", pathname: "/d/selected-doc" });
    expect(host).toHaveTextContent("Drawer open");
  });

  it("rejects an old opening callback after close and a fresh opening under the same owner", async () => {
    render();
    await openSearch();
    const oldNavigate = control.onDocumentNavigate;
    if (!oldNavigate) throw new Error("Captured navigation missing");
    flushSync(() => control.setOpen(false));
    await openSearch();
    expect(control.onDocumentNavigate).not.toBe(oldNavigate);
    flushSync(() => oldNavigate("/d/selected-doc"));
    render({ pathname: "/d/selected-doc" });
    expect(host).toHaveTextContent("Drawer open");
    selectDocument();
    expect(host).toHaveTextContent("Drawer closed");
  });

  it("rejects a retired A opening across A to B to A and admits a newly captured current opening", async () => {
    render();
    await openSearch();
    const oldNavigate = control.onDocumentNavigate;
    if (!oldNavigate) throw new Error("Captured navigation missing");
    render({ userId: "user-b" });
    expect(control.onDocumentNavigate).toBeNull();
    render();
    flushSync(() => oldNavigate("/d/selected-doc"));
    render({ pathname: "/d/selected-doc" });
    expect(host).toHaveTextContent("Drawer open");
    flushSync(() => control.setOpen(false));
    await openSearch();
    selectDocument();
    expect(host).toHaveTextContent("Drawer closed");
  });

  it("clears the opening callback when the sidebar navigation provider unmounts", async () => {
    render();
    await openSearch();
    const oldNavigate = control.onDocumentNavigate;
    if (!oldNavigate) throw new Error("Captured navigation missing");
    render({ showOpener: false });
    expect(control.open).toBe(true);
    expect(control.onDocumentNavigate).toBeNull();
    flushSync(() => oldNavigate("/d/selected-doc"));
    render({ pathname: "/d/selected-doc", showOpener: false });
    expect(host).toHaveTextContent("Drawer open");
  });

  it("reveals a selected folder without registering a document destination or adding document reads", async () => {
    vi.mocked(WIKI_READ_API.driveSearch).mockResolvedValue([{ ...RESULT, mimeType: MIME_FOLDER }]);
    render();
    await openSearch();
    selectDocument();
    expect(control.open).toBe(false);
    expect(ROUTER.push).not.toHaveBeenCalled();
    expect(WIKI_READ_API.getFile).not.toHaveBeenCalled();
    expect(WIKI_READ_API.driveSearchSnippet).not.toHaveBeenCalled();
    render({ pathname: "/d/selected-doc" });
    expect(host).toHaveTextContent("Drawer open");
  });

  it("keeps the drawer open for an external Form selection", async () => {
    vi.mocked(WIKI_READ_API.driveSearch).mockResolvedValue([{ ...RESULT, mimeType: MIME_FORM }]);
    const open = vi.spyOn(window, "open").mockReturnValue(null);
    render();
    await openSearch();
    selectDocument();
    expect(open).toHaveBeenCalledExactlyOnceWith(
      "https://docs.google.com/forms/d/selected-doc/edit",
      "_blank",
      "noopener,noreferrer",
    );
    expect(ROUTER.push).not.toHaveBeenCalled();
    expect(WIKI_READ_API.getFile).not.toHaveBeenCalled();
    render({ pathname: "/d/selected-doc" });
    expect(host).toHaveTextContent("Drawer open");
  });

  it.each([{ ctrlKey: true }, { metaKey: true }, { shiftKey: true }, { altKey: true }])(
    "keeps modified selection out of sidebar navigation intent ($ctrlKey $metaKey $shiftKey $altKey)",
    async (modifiers) => {
      render();
      await openSearch();
      selectDocument(modifiers);
      render({ pathname: "/d/selected-doc" });
      expect(host).toHaveTextContent("Drawer open");
    },
  );

  it("preserves a consumer-cancelled Enter selection without closing or navigation", async () => {
    render();
    await openSearch();
    input().addEventListener("keydown", (event) => event.preventDefault(), { once: true });
    selectDocument();
    expect(control.open).toBe(true);
    expect(ROUTER.push).not.toHaveBeenCalled();
    expect(host).toHaveTextContent("Drawer open");
  });

  it("uses the same accepted commit contract for the preview Open button", async () => {
    render();
    await openSearch();
    flushSync(() => button("Open").click());
    expect(ROUTER.push).toHaveBeenCalledExactlyOnceWith("/d/selected-doc");
    expect(host).toHaveTextContent("Drawer open");
    render({ pathname: "/d/selected-doc" });
    await vi.waitFor(() => expect(host).toHaveTextContent("Drawer closed"));
  });
});

function button(text: string): HTMLButtonElement {
  const element = [...document.querySelectorAll("button")].find(
    (candidate) => candidate.textContent.trim() === text,
  );
  if (!element) throw new Error(`${text} control missing`);
  return element;
}

function input(): HTMLInputElement {
  const element = document.querySelector('[aria-label="Search Drive"]');
  if (!(element instanceof HTMLInputElement)) throw new TypeError("Search input missing");
  return element;
}

function typeQuery(value: string): void {
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

function selectDocument(modifiers: KeyboardEventInit = {}): void {
  flushSync(() =>
    input().dispatchEvent(
      new KeyboardEvent("keydown", {
        bubbles: true,
        cancelable: true,
        key: "Enter",
        ...modifiers,
      }),
    ),
  );
}

function dispatchHotkey(): void {
  flushSync(() =>
    document.body.dispatchEvent(
      new KeyboardEvent("keydown", {
        bubbles: true,
        cancelable: true,
        code: "KeyK",
        ctrlKey: true,
        key: "k",
      }),
    ),
  );
  document.body.dispatchEvent(
    new KeyboardEvent("keyup", {
      bubbles: true,
      code: "KeyK",
      ctrlKey: true,
      key: "k",
    }),
  );
}

async function openHotkeySearch() {
  flushSync(() => button("Open drawer").click());
  flushSync(() => document.body.focus());
  dispatchHotkey();
  await vi.advanceTimersByTimeAsync(20);
  expect(input()).toHaveFocus();
  expect(control.onDocumentNavigate).toBeTypeOf("function");
  typeQuery("selected");
  await vi.advanceTimersByTimeAsync(250);
  await vi.advanceTimersByTimeAsync(0);
  expect(document.body).toHaveTextContent(RESULT.name);
}
