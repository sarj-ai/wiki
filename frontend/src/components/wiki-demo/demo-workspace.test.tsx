import { fireEvent, render, type RenderResult, screen, within } from "@testing-library/react";
import { act } from "react";

import { DEMO_FILES, demoFileFor, demoHref } from "./demo-data";
import { DemoWorkspace } from "./demo-workspace";

const ROUTING = vi.hoisted(() => ({ push: vi.fn(), search: "" }));
const COOKIE_WRITE = vi.fn().mockResolvedValue(undefined);
const HTTP = vi.fn<typeof fetch>();
const PUBLIC_GOOGLE_LINKS = new Set(DEMO_FILES.flatMap((file) => [file.viewUrl, file.downloadUrl]));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: ROUTING.push }),
  useSearchParams: () => new URLSearchParams(ROUTING.search),
}));
vi.mock("next-themes", () => ({ useTheme: () => ({ resolvedTheme: "light", setTheme: vi.fn() }) }));
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

  it("opens the overview without authentication, private requests or a context banner", async () => {
    await mount();
    expect(screen.getByText("Read-only workspace")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Overview");
    expect(screen.getByTitle(/read-only preview/)).toHaveAttribute(
      "src",
      demoFileFor(null).previewUrl,
    );
    expect(screen.getByText("Public demo · Read only")).toBeInTheDocument();
    expect(screen.queryByText(demoFileFor(null).description)).toBeNull();
    expect(screen.queryByRole("group", { name: "Compare versions" })).toBeNull();
    expect(
      screen.queryByText(/Before|After|Processing record|Review note|fewer words/u),
    ).toBeNull();
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.queryByRole("form")).toBeNull();
    expect(HTTP).not.toHaveBeenCalled();
    expect(COOKIE_WRITE).not.toHaveBeenCalled();
    for (const link of screen.getAllByRole("link")) {
      const href = link.getAttribute("href") ?? "";
      const publicGoogleFile =
        PUBLIC_GOOGLE_LINKS.has(href) &&
        /^https:\/\/(?:docs|drive)\.google\.com\//u.test(href) &&
        !new URL(href).pathname.endsWith("/edit");
      expect(href.startsWith("/demo") || href === "#demo-document" || publicGoogleFile).toBe(true);
    }
  });

  it("shows one Drive with exactly three direct file rows and no folder controls", async () => {
    await mount();
    const drive = screen.getByRole("navigation", { name: "Demo Drive" });
    const files = within(drive)
      .getAllByRole("link")
      .filter((link) => link.getAttribute("href")?.startsWith("/demo?file="));
    expect(files.map((link) => link.textContent)).toEqual([
      "Overview",
      "Wiki stats",
      "How Wiki works",
    ]);
    expect(files[0]).toHaveAttribute("aria-current", "page");
    expect(within(drive).getByText("Sarj Wiki")).toBeInTheDocument();
    expect(within(drive).getAllByRole("list")).toHaveLength(1);
    expect(within(drive).queryByRole("button", { name: "Sort files" })).toBeNull();
  });

  it.each(["Create file", "Delete", "Rename", "Move", "Polish document", "Account menu"])(
    "never exposes the private action: %s",
    async (action) => {
      await mount();
      expect(screen.queryByRole("button", { name: action })).toBeNull();
      expect(HTTP).not.toHaveBeenCalled();
      expect(COOKIE_WRITE).not.toHaveBeenCalled();
    },
  );

  it.each([
    "unknown",
    "https://docs.google.com/document/d/private-id/preview",
    "https://example.com/private",
    "../d/private",
    "<script>alert(1)</script>",
  ])("uses only the trusted overview for an unknown selector: %s", async (selector) => {
    ROUTING.search = new URLSearchParams({ file: selector }).toString();
    await mount();
    const frame = screen.getByTitle(/read-only preview/);
    expect(frame).toHaveAttribute("src", demoFileFor(null).previewUrl);
    expect(frame).not.toHaveAttribute("srcdoc");
    expect(frame).toHaveAttribute("sandbox", "allow-scripts allow-same-origin");
    expect(HTTP).not.toHaveBeenCalled();
  });

  it.each(DEMO_FILES)("isolates the real $id preview without a private request", async (file) => {
    ROUTING.search = new URLSearchParams({ file: file.id }).toString();
    await mount();
    const frame = screen.getByTitle(/read-only preview/);
    expect(frame).toHaveAttribute("referrerpolicy", "no-referrer");
    expect(frame).toHaveAttribute("sandbox", "allow-scripts allow-same-origin");
    expect(frame).toHaveAttribute("src", file.previewUrl);
    expect(frame).not.toHaveAttribute("srcdoc");
    expect(frame.getAttribute("src")).toMatch(/\/preview(?:#gid=[0-9]{1,10})?$/u);
    expect(HTTP).not.toHaveBeenCalled();
    expect(COOKIE_WRITE).not.toHaveBeenCalled();
  });

  it.each(DEMO_FILES)("offers the real $id download without a mutation", async (file) => {
    ROUTING.search = new URLSearchParams({ file: file.id }).toString();
    await mount();
    const download = screen.getByRole("link", { name: `Download ${file.name}` });
    expect(download).toHaveAttribute("href", file.downloadUrl);
    expect(download).toHaveAttribute("download", file.downloadFilename);
    expect(download).toHaveAttribute("target", "_blank");
    expect(download).toHaveAttribute("rel", "noopener noreferrer");
    expect(HTTP).not.toHaveBeenCalled();
    expect(COOKIE_WRITE).not.toHaveBeenCalled();
  });

  it("filters local search and opens its selected file with the keyboard", async () => {
    await mount();
    fireEvent.keyDown(window, { ctrlKey: true, key: "k" });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    const dialog = screen.getByRole("dialog", { name: "Search Drive" });
    const input = within(dialog).getByRole("combobox", { name: "Search Drive" });
    fireEvent.change(input, { target: { value: "spreadsheet" } });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    const result = within(dialog).getByRole("option", { name: /Wiki stats/u });
    expect(result).toBeVisible();
    expect(within(dialog).getAllByRole("option")).toHaveLength(1);
    fireEvent.keyDown(input, { key: "ArrowDown" });
    fireEvent.keyDown(input, { key: "Enter" });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(ROUTING.push).toHaveBeenCalledExactlyOnceWith(demoHref(demoFileFor("wiki-statistics")));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(HTTP).not.toHaveBeenCalled();
  });

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
