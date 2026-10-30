import { fireEvent, render, type RenderResult, screen, within } from "@testing-library/react";
import { act } from "react";

import { DemoWorkspace } from "./demo-workspace";

const ROUTING = vi.hoisted(() => ({ push: vi.fn(), search: "" }));
const COOKIE_WRITE = vi.fn().mockResolvedValue(undefined);
const PUBLIC_FETCH = vi.fn<typeof fetch>();
const PREVIEW_HTML = "<!doctype html><html><body><main><h1>Public sample</h1></main></body></html>";
const PREVIEW_PATHS = new Set([
  "/demo/files/northstar-launch-formatted.html",
  "/demo/files/northstar-launch-original.html",
  "/demo/files/northstar-kickoff.html",
  "/demo/files/northstar-budget.html",
  "/demo/files/northstar-rollout.html",
]);

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: ROUTING.push }),
  useSearchParams: () => new URLSearchParams(ROUTING.search),
}));
vi.mock("next-themes", () => ({ useTheme: () => ({ resolvedTheme: "light", setTheme: vi.fn() }) }));
vi.mock("@/hooks/use-is-mobile", () => ({ useIsMobile: () => false }));

function onlyPublicRequests(): boolean {
  return PUBLIC_FETCH.mock.calls.every(
    ([url, options]) =>
      typeof url === "string" &&
      PREVIEW_PATHS.has(url) &&
      options?.credentials === "omit" &&
      (options.method ?? "GET") === "GET" &&
      options.redirect === "error" &&
      options.signal !== undefined &&
      !new Headers(options.headers).has("authorization"),
  );
}

describe("public read-only Wiki demo", () => {
  let view: RenderResult | undefined;

  beforeEach(() => {
    view = undefined;
    ROUTING.search = "";
    ROUTING.push.mockClear();
    COOKIE_WRITE.mockClear();
    PUBLIC_FETCH.mockReset();
    PUBLIC_FETCH.mockImplementation(async (url, options) => {
      await Promise.resolve();
      if (typeof url !== "string" || !PREVIEW_PATHS.has(url) || options?.credentials !== "omit") {
        throw new Error("Private API request from public demo");
      }
      return new Response(PREVIEW_HTML, { headers: { "content-type": "text/html" } });
    });
    vi.useFakeTimers();
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    vi.stubGlobal("fetch", PUBLIC_FETCH);
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

  it("opens without auth and only reads public samples", async () => {
    await mount();
    expect(screen.getByText("Read-only workspace")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
      "Launch brief · formatted.docx",
    );
    expect(PUBLIC_FETCH).toHaveBeenCalledOnce();
    expect(onlyPublicRequests()).toBe(true);
    expect(COOKIE_WRITE).not.toHaveBeenCalled();
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.queryByRole("form")).toBeNull();
    for (const link of screen.getAllByRole("link")) {
      const href = link.getAttribute("href");
      const publicDriveFile = /^https:\/\/drive\.google\.com\/file\/d\/[\w-]+\/view(?:\?|$)/u.test(
        href ?? "",
      );
      expect(href?.startsWith("/demo") || href === "#demo-document" || publicDriveFile).toBe(true);
    }
    const publicDriveLink = screen.getByRole("link", {
      name: "Open sample in Google Drive",
    });
    expect(publicDriveLink).toHaveAttribute("target", "_blank");
    expect(publicDriveLink).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("exposes a named semantic workspace navigation landmark", async () => {
    await mount();
    expect(screen.getByRole("navigation", { name: "Sample workspace" })).toBeInTheDocument();
  });

  it.each(["Create file", "Delete", "Rename", "Move", "Polish document", "Account menu"])(
    "never exposes the private action: %s",
    async (action) => {
      await mount();
      expect(screen.queryByRole("button", { name: action })).toBeNull();
      expect(onlyPublicRequests()).toBe(true);
    },
  );

  it.each(["unknown", "https://example.com/private", "../d/private", "<script>alert(1)</script>"])(
    "uses only the trusted clean sample for unknown selector: %s",
    async (selector) => {
      ROUTING.search = new URLSearchParams({ file: selector }).toString();
      await mount();
      expect(PUBLIC_FETCH).toHaveBeenCalledExactlyOnceWith(
        "/demo/files/northstar-launch-formatted.html",
        expect.objectContaining({ credentials: "omit" }),
      );
      expect(screen.getByTitle(/read-only preview/)).toHaveAttribute("srcdoc", PREVIEW_HTML);
      expect(onlyPublicRequests()).toBe(true);
    },
  );

  it.each([
    {
      id: "launch-brief-clean",
      name: "Launch brief · formatted.docx",
      preview: "northstar-launch-formatted.html",
      download: "northstar-launch-formatted.docx",
    },
    {
      id: "launch-brief-messy",
      name: "Launch brief · original.docx",
      preview: "northstar-launch-original.html",
      download: "northstar-launch-original.docx",
    },
    {
      id: "kickoff",
      name: "Project kickoff.pptx",
      preview: "northstar-kickoff.html",
      download: "northstar-kickoff.pptx",
    },
    {
      id: "budget",
      name: "Launch budget.xlsx",
      preview: "northstar-budget.html",
      download: "northstar-budget.xlsx",
    },
    {
      id: "status",
      name: "Rollout status.html",
      preview: "northstar-rollout.html",
      download: "northstar-rollout.html",
    },
  ])(
    "isolates the $id preview and offers its real download",
    async ({ download, id, name, preview }) => {
      ROUTING.search = new URLSearchParams({ file: id }).toString();
      await mount();
      const frame = screen.getByTitle(/read-only preview/);
      expect(frame).toHaveAttribute("sandbox", "");
      expect(frame).toHaveAttribute("referrerpolicy", "no-referrer");
      expect(frame).toHaveAttribute("srcdoc", PREVIEW_HTML);
      expect(frame).not.toHaveAttribute("src");
      expect(PUBLIC_FETCH).toHaveBeenCalledExactlyOnceWith(
        `/demo/files/${preview}`,
        expect.any(Object),
      );
      expect(screen.getByRole("link", { name: `Download ${name}` })).toHaveAttribute(
        "href",
        `/demo/files/${download}`,
      );
      expect(screen.getByRole("link", { name: `Download ${name}` })).toHaveAttribute(
        "download",
        download,
      );
      expect(onlyPublicRequests()).toBe(true);
    },
  );

  it("compares the same brief without invoking a formatting mutation", async () => {
    await mount();
    const comparison = screen.getByRole("group", { name: "Compare document formatting" });
    expect(within(comparison).getByRole("link", { name: "After" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(within(comparison).getByRole("link", { name: "Before" })).toHaveAttribute(
      "href",
      "/demo?file=launch-brief-messy",
    );
    expect(onlyPublicRequests()).toBe(true);
  });

  it("opens local search and routes its chosen result only within the demo", async () => {
    await mount();
    fireEvent.keyDown(window, { ctrlKey: true, key: "k" });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    const dialog = screen.getByRole("dialog");
    fireEvent.click(within(dialog).getByRole("option", { name: /Launch budget/ }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(ROUTING.push).toHaveBeenCalledExactlyOnceWith("/demo?file=budget");
    expect(onlyPublicRequests()).toBe(true);
  });
});
