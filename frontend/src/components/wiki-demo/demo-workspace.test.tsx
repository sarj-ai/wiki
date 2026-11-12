import {
  fireEvent,
  render,
  type RenderResult,
  screen,
  within,
} from "@testing-library/react";
import { act } from "react";

import {
  DEMO_FILES,
  DEMO_SEARCH_SUGGESTION,
  demoFileFor,
  demoHref,
} from "./demo-data";
import { DemoWorkspace } from "./demo-workspace";

const ROUTING = vi.hoisted(() => ({ push: vi.fn(), search: "" }));
const COOKIE_WRITE = vi.fn().mockResolvedValue(undefined);
const HTTP = vi.fn<typeof fetch>();
const PUBLIC_GOOGLE_LINKS = new Set(
  DEMO_FILES.flatMap((file) => [file.viewUrl, file.downloadUrl]),
);

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: ROUTING.push }),
  useSearchParams: () => new URLSearchParams(ROUTING.search),
}));
vi.mock("next-themes", () => ({
  useTheme: () => ({ resolvedTheme: "light", setTheme: vi.fn() }),
}));
vi.mock("@/hooks/use-is-mobile", () => ({ useIsMobile: () => false }));

describe("public read-only Wiki demo", () => {
  let view: RenderResult | undefined;

  beforeEach(() => {
    view = undefined;
    ROUTING.search = "";
    ROUTING.push.mockClear();
    COOKIE_WRITE.mockClear();
    HTTP.mockReset();
    vi.useFakeTimers();
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    vi.stubGlobal("fetch", HTTP);
    vi.stubGlobal("cookieStore", { set: COOKIE_WRITE });
  });

  afterEach(async () => {
    view?.unmount();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  async function mount() {
    view = render(<DemoWorkspace />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
  }

  it("opens Numbers Every Programmer Should Know without authentication, private requests or a context banner", async () => {
    await mount();
    expect(screen.getByText("Read-only workspace")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
      "Numbers Every Programmer Should Know",
    );
    expect(screen.getByTitle(/read-only preview/)).toHaveAttribute(
      "src",
      demoFileFor(null).previewUrl,
    );
    expect(screen.getByText("Public demo · Read only")).toBeInTheDocument();
    expect(screen.queryByText(demoFileFor(null).description)).toBeNull();
    expect(
      screen.queryByRole("group", { name: "Compare versions" }),
    ).toBeNull();
    expect(
      screen.queryByText(
        /Before|After|Processing record|Review note|fewer words/u,
      ),
    ).toBeNull();
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.queryByRole("heading", { name: "Search Drive" })).toBeNull();
    expect(screen.queryByRole("form")).toBeNull();
    expect(HTTP).not.toHaveBeenCalled();
    expect(COOKIE_WRITE).not.toHaveBeenCalled();
    for (const link of screen.getAllByRole("link")) {
      const href = link.getAttribute("href") ?? "";
      const publicGoogleFile =
        PUBLIC_GOOGLE_LINKS.has(href) &&
        /^https:\/\/(?:docs|drive)\.google\.com\//u.test(href) &&
        !new URL(href).pathname.endsWith("/edit");
      expect(
        href.startsWith("/demo") ||
          href === "#demo-document" ||
          publicGoogleFile,
      ).toBe(true);
    }
  });

  it("shows one Drive with exactly four direct file rows and no folder controls", async () => {
    await mount();
    const drive = screen.getByRole("navigation", { name: "Demo Drive" });
    const files = within(drive)
      .getAllByRole("link")
      .filter((link) => link.getAttribute("href")?.startsWith("/demo?file="));
    expect(files.map((link) => link.textContent)).toEqual([
      "Numbers Every Programmer Should Know",
      "AI draft",
      "Numbers workbook",
      "Numbers slides",
    ]);
    expect(files[0]).toHaveAttribute("aria-current", "page");
    expect(within(drive).getByText("Sarj Wiki")).toBeInTheDocument();
    expect(within(drive).getAllByRole("list")).toHaveLength(1);
    expect(
      within(drive).queryByRole("button", { name: "Sort files" }),
    ).toBeNull();
  });

  it.each([
    "Create file",
    "Delete",
    "Rename",
    "Move",
    "Polish document",
    "Account menu",
  ])("never exposes the private action: %s", async (action) => {
    await mount();
    expect(screen.queryByRole("button", { name: action })).toBeNull();
    expect(HTTP).not.toHaveBeenCalled();
    expect(COOKIE_WRITE).not.toHaveBeenCalled();
  });

  it.each([
    "unknown",
    "https://docs.google.com/document/d/private-id/preview",
    "https://example.com/private",
    "../d/private",
    "<script>alert(1)</script>",
  ])(
    "uses only the trusted Numbers Every Programmer Should Know for an unknown selector: %s",
    async (selector) => {
      ROUTING.search = new URLSearchParams({ file: selector }).toString();
      await mount();
      const frame = screen.getByTitle(/read-only preview/);
      expect(frame).toHaveAttribute("src", demoFileFor(null).previewUrl);
      expect(frame).not.toHaveAttribute("srcdoc");
      expect(frame).toHaveAttribute(
        "sandbox",
        "allow-scripts allow-same-origin",
      );
      expect(HTTP).not.toHaveBeenCalled();
    },
  );

  it.each(DEMO_FILES)(
    "isolates the real $id preview without a private request",
    async (file) => {
      ROUTING.search = new URLSearchParams({ file: file.id }).toString();
      await mount();
      const frame = screen.getByTitle(/read-only preview/);
      expect(frame).toHaveAttribute("referrerpolicy", "no-referrer");
      expect(frame).toHaveAttribute(
        "sandbox",
        "allow-scripts allow-same-origin",
      );
      expect(frame).toHaveAttribute("src", file.previewUrl);
      expect(frame).not.toHaveAttribute("srcdoc");
      expect(frame.getAttribute("src")).toMatch(
        /\/preview(?:#gid=[0-9]{1,10})?$/u,
      );
      expect(HTTP).not.toHaveBeenCalled();
      expect(COOKIE_WRITE).not.toHaveBeenCalled();
    },
  );

  it.each(DEMO_FILES)(
    "switches $id views and reloads without changing permissions or calling private APIs",
    async (file) => {
      ROUTING.search = new URLSearchParams({ file: file.id }).toString();
      await mount();
      const preview = screen.getByRole("button", { name: "Preview" });
      const edit = screen.getByRole("button", { name: "Edit" });
      expect(preview).toHaveAttribute("aria-pressed", "true");
      expect(edit).toBeEnabled();
      fireEvent.click(edit);
      const editor = screen.getByTitle(/ — editor view$/);
      expect(editor).toHaveAttribute(
        "src",
        file.previewUrl.replace("/preview", "/edit"),
      );
      expect(editor).toHaveAttribute(
        "sandbox",
        "allow-scripts allow-same-origin",
      );
      expect(editor).toHaveAttribute("referrerpolicy", "no-referrer");
      expect(edit).toHaveAttribute("aria-pressed", "true");
      fireEvent.click(screen.getByRole("button", { name: "Reload" }));
      const reloaded = screen.getByTitle(/ — editor view$/);
      expect(reloaded).not.toBe(editor);
      expect(reloaded).toHaveAttribute(
        "src",
        file.previewUrl.replace("/preview", "/edit"),
      );
      expect(edit).toHaveAttribute("aria-pressed", "true");
      fireEvent.click(preview);
      expect(screen.getByTitle(/read-only preview/)).toHaveAttribute(
        "src",
        file.previewUrl,
      );
      expect(screen.getByText("Public demo · Read only")).toBeInTheDocument();
      expect(HTTP).not.toHaveBeenCalled();
      expect(COOKIE_WRITE).not.toHaveBeenCalled();
    },
  );

  it("starts each newly selected sample in preview mode", async () => {
    await mount();
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    ROUTING.search = "file=latency-workbook";
    view?.rerender(<DemoWorkspace />);
    expect(screen.getByRole("button", { name: "Preview" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByTitle(/read-only preview/)).toHaveAttribute(
      "src",
      demoFileFor("latency-workbook").previewUrl,
    );
    ROUTING.search = "file=latency-guide";
    view?.rerender(<DemoWorkspace />);
    expect(screen.queryByTitle(/ — editor view$/)).toBeNull();
    expect(screen.getByRole("button", { name: "Preview" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it.each(DEMO_FILES)(
    "offers the real $id download without a mutation",
    async (file) => {
      ROUTING.search = new URLSearchParams({ file: file.id }).toString();
      await mount();
      const download = screen.getByRole("link", {
        name: `Download ${file.name}`,
      });
      expect(download).toHaveAttribute("href", file.downloadUrl);
      expect(download).toHaveAttribute("download", file.downloadFilename);
      expect(download).toHaveAttribute("target", "_blank");
      expect(download).toHaveAttribute("rel", "noopener noreferrer");
      expect(HTTP).not.toHaveBeenCalled();
      expect(COOKIE_WRITE).not.toHaveBeenCalled();
    },
  );

  it("filters local search and opens its selected file with the keyboard", async () => {
    await mount();
    fireEvent.keyDown(window, { ctrlKey: true, key: "k" });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    const dialog = screen.getByRole("dialog", { name: "Search Drive" });
    const input = within(dialog).getByRole("combobox", {
      name: "Search Drive",
    });
    fireEvent.change(input, { target: { value: "spreadsheet" } });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    const result = within(dialog).getByRole("option", {
      name: /Numbers workbook/u,
    });
    expect(result).toBeVisible();
    expect(within(dialog).getAllByRole("option")[0]).toBe(result);
    fireEvent.keyDown(input, { key: "ArrowDown" });
    fireEvent.keyDown(input, { key: "Home" });
    fireEvent.keyDown(input, { key: "Enter" });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(ROUTING.push).toHaveBeenCalledExactlyOnceWith(
      demoHref(demoFileFor("latency-workbook")),
    );
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(HTTP).not.toHaveBeenCalled();
  });

  it("closes search with Enter without opening its highlighted result", async () => {
    await mount();
    fireEvent.click(screen.getByRole("button", { name: "Open search" }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    const close = screen.getByRole("button", { name: "Close search" });
    fireEvent.keyDown(close, { key: "Enter" });
    expect(ROUTING.push).not.toHaveBeenCalled();
    fireEvent.click(close);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(ROUTING.push).not.toHaveBeenCalled();
  });

  it.each([{ ctrlKey: true }, { metaKey: true }])(
    "toggles search closed from its input with the same shortcut: %j",
    async (modifier) => {
      await mount();
      fireEvent.keyDown(window, { key: "k", ...modifier });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });
      const input = screen.getByRole("combobox", { name: "Search Drive" });
      fireEvent.keyDown(input, { key: "k", ...modifier });
      expect(screen.queryByRole("dialog")).toBeNull();
      expect(ROUTING.push).not.toHaveBeenCalled();
    },
  );

  it("announces an empty search through a persistent live region", async () => {
    await mount();
    fireEvent.click(screen.getByRole("button", { name: "Open search" }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    const dialog = screen.getByRole("dialog", { name: "Search Drive" });
    const announcement = within(dialog).getByRole("status");
    expect(announcement).toBeEmptyDOMElement();
    fireEvent.change(within(dialog).getByRole("combobox"), {
      target: { value: "no-file-matches-this-query-9327" },
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(within(dialog).getByRole("status")).toBe(announcement);
    expect(announcement).toHaveTextContent("No files found");
    expect(announcement).toHaveTextContent(DEMO_SEARCH_SUGGESTION);
    expect(announcement).toHaveAttribute("aria-live", "polite");
  });

  it("preserves keyboard focus on the toolbar when retrying a slow preview", async () => {
    await mount();
    const oldFrame = screen.getByTitle(/read-only preview/);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    const retry = screen.getByRole("button", { name: "Try again" });
    retry.focus();
    fireEvent.click(retry);
    expect(screen.getByRole("button", { name: "Reload" })).toHaveFocus();
    expect(screen.getByTitle(/read-only preview/)).not.toBe(oldFrame);
    expect(HTTP).not.toHaveBeenCalled();
  });

  it.each([{ altKey: true }, { shiftKey: true }])(
    "preserves modified keyboard shortcuts: %j",
    async (modifier) => {
      await mount();
      const event = new KeyboardEvent("keydown", {
        cancelable: true,
        ctrlKey: true,
        key: "k",
        ...modifier,
      });
      fireEvent(window, event);
      expect(event.defaultPrevented).toBe(false);
      expect(screen.queryByRole("dialog")).toBeNull();
      expect(ROUTING.push).not.toHaveBeenCalled();
    },
  );

  it.each([
    { handled: false, label: "held", repeat: true },
    { handled: true, label: "already handled", repeat: false },
  ])("keeps search open for $label shortcuts", async ({ handled, repeat }) => {
    await mount();
    fireEvent.keyDown(window, { ctrlKey: true, key: "k" });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    const dialog = screen.getByRole("dialog", { name: "Search Drive" });
    const event = new KeyboardEvent("keydown", {
      cancelable: true,
      ctrlKey: true,
      key: "k",
      repeat,
    });
    if (handled) event.preventDefault();
    fireEvent(window, event);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByRole("dialog", { name: "Search Drive" })).toBe(dialog);
    expect(ROUTING.push).not.toHaveBeenCalled();
    expect(HTTP).not.toHaveBeenCalled();
  });
});
