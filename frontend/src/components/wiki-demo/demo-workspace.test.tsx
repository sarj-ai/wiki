import { webcrypto } from "node:crypto";

import { fireEvent, render, type RenderResult, screen, within } from "@testing-library/react";
import { act } from "react";

import { DEMO_FILES, demoAssetUrl, demoFileFor } from "./demo-data";
import { DemoWorkspace } from "./demo-workspace";

const ROUTING = vi.hoisted(() => ({ push: vi.fn(), search: "" }));
const COOKIE_WRITE = vi.fn().mockResolvedValue(undefined);
const PUBLIC_FETCH = vi.fn<typeof fetch>();
const PREVIEW_HTML = "<!doctype html><html><body>Public sample</body></html>";
const HTML_SHA256 = "1b47b4fc9abe3730ae8b985a77767ae39f684f593bb8a8b64920c48b78b06695";
const DEFAULT_PREVIEW =
  "https://docs.google.com/document/d/11ZKT_SKqzN17KzjRQ_iyoqgM6HjYEdFvokNOQFpExlk/preview";
const PREVIEW_PATHS = new Set(
  DEMO_FILES.flatMap((file) =>
    file.previewUrl === undefined && file.previewFilename ? demoAssetUrl(file.previewFilename) : [],
  ),
);
const PUBLIC_GOOGLE_LINKS = new Set(
  DEMO_FILES.flatMap((file) =>
    [file.viewUrl, file.downloadUrl].filter((url): url is string => url !== undefined),
  ),
);
const NATIVE_FILES = DEMO_FILES.filter((file) => file.previewUrl !== undefined);
const OFFICE_FILES = DEMO_FILES.filter((file) =>
  file.mimeType.startsWith("application/vnd.openxmlformats-"),
);
const REVIEW_FILES = DEMO_FILES.filter((file) => file.processingNeedsReview);

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
      url.startsWith("/demo/files/") &&
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
      if (
        typeof url !== "string" ||
        !url.startsWith("/demo/files/") ||
        !PREVIEW_PATHS.has(url) ||
        options?.credentials !== "omit"
      ) {
        throw new Error("Private API request from public demo");
      }
      return url.endsWith(".json")
        ? Response.json({ html: PREVIEW_HTML, sha256: HTML_SHA256 })
        : new Response(PREVIEW_HTML, { headers: { "content-type": "text/html" } });
    });
    vi.useFakeTimers();
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    vi.stubGlobal("fetch", PUBLIC_FETCH);
    vi.stubGlobal("crypto", webcrypto);
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
    await vi.waitFor(() => {
      expect(screen.getByTitle(/read-only preview/)).not.toBeNull();
    });
  }

  it("opens the recorded nightingale result without auth or private API requests", async () => {
    await mount();
    expect(screen.getByText("Read-only workspace")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(/nightingale/i);
    expect(screen.getByTitle(/read-only preview/)).toHaveAttribute("src", DEFAULT_PREVIEW);
    expect(PUBLIC_FETCH).not.toHaveBeenCalled();
    expect(COOKIE_WRITE).not.toHaveBeenCalled();
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.queryByRole("form")).toBeNull();
    for (const link of screen.getAllByRole("link")) {
      const href = link.getAttribute("href") ?? "";
      const publicGoogleFile =
        PUBLIC_GOOGLE_LINKS.has(href) &&
        /^https:\/\/(?:docs|drive)\.google\.com\//u.test(href) &&
        !new URL(href).pathname.endsWith("/edit");
      expect(href.startsWith("/demo") || href === "#demo-document" || publicGoogleFile).toBe(true);
    }
    const publicDriveLink = screen.getByRole("link", { name: "Open sample in Google Drive" });
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
      expect(COOKIE_WRITE).not.toHaveBeenCalled();
    },
  );

  it.each([
    "unknown",
    "https://docs.google.com/document/d/private-id/preview",
    "https://example.com/private",
    "../d/private",
    "<script>alert(1)</script>",
  ])("uses only the trusted recorded result for unknown selector: %s", async (selector) => {
    ROUTING.search = new URLSearchParams({ file: selector }).toString();
    await mount();
    const frame = screen.getByTitle(/read-only preview/);
    expect(frame).toHaveAttribute("src", DEFAULT_PREVIEW);
    expect(frame).not.toHaveAttribute("srcdoc");
    expect(frame).toHaveAttribute("sandbox", "allow-scripts allow-same-origin");
    expect(PUBLIC_FETCH).not.toHaveBeenCalled();
    expect(onlyPublicRequests()).toBe(true);
  });

  it.each(NATIVE_FILES)(
    "isolates the $id native preview without a private request",
    async (file) => {
      ROUTING.search = new URLSearchParams({ file: file.id }).toString();
      await mount();
      const frame = screen.getByTitle(/read-only preview/);
      expect(frame).toHaveAttribute("referrerpolicy", "no-referrer");
      expect(frame).toHaveAttribute("sandbox", "allow-scripts allow-same-origin");
      expect(frame).toHaveAttribute("src", file.previewUrl);
      expect(frame).not.toHaveAttribute("srcdoc");
      expect(frame.getAttribute("src")).toMatch(/\/preview$/u);
      expect(PUBLIC_FETCH).not.toHaveBeenCalled();
      expect(COOKIE_WRITE).not.toHaveBeenCalled();
    },
  );

  it.each(OFFICE_FILES)("uses the genuine Office file preview for $id", async (file) => {
    ROUTING.search = new URLSearchParams({ file: file.id }).toString();
    await mount();
    expect(screen.getByTitle(/read-only preview/).getAttribute("src")).toMatch(
      /^https:\/\/drive\.google\.com\/file\/d\/[A-Za-z0-9_-]+\/preview$/u,
    );
  });

  it("loads checked HTML with citation popups and no privileged preview capabilities", async () => {
    ROUTING.search = new URLSearchParams({ file: "nightingale-html" }).toString();
    await mount();
    const frame = screen.getByTitle(/read-only preview/);
    expect(frame).toHaveAttribute("sandbox", "allow-popups allow-popups-to-escape-sandbox");
    const permissions = frame.getAttribute("sandbox")?.split(" ");
    expect(permissions).not.toContain("allow-scripts");
    expect(permissions).not.toContain("allow-same-origin");
    expect(permissions).not.toContain("allow-forms");
    expect(permissions).not.toContain("allow-top-navigation");
    expect(permissions).not.toContain("allow-top-navigation-by-user-activation");
    expect(permissions).not.toContain("allow-top-navigation-to-custom-protocols");
    expect(frame).toHaveAttribute("srcdoc", PREVIEW_HTML);
    expect(frame).not.toHaveAttribute("src");
    expect(PUBLIC_FETCH).toHaveBeenCalledExactlyOnceWith(
      "/demo/files/nightingale-field-guide-preview.json",
      expect.objectContaining({ credentials: "omit", headers: { Accept: "application/json" } }),
    );
    expect(onlyPublicRequests()).toBe(true);
  });

  it.each(DEMO_FILES)("offers the real $id download without a mutation", async (file) => {
    ROUTING.search = new URLSearchParams({ file: file.id }).toString();
    await mount();
    const download = screen.getByRole("link", { name: `Download ${file.name}` });
    expect(download).toHaveAttribute(
      "href",
      file.downloadUrl ?? demoAssetUrl(file.downloadFilename),
    );
    expect(download).toHaveAttribute("download", file.downloadFilename);
    expect(onlyPublicRequests()).toBe(true);
    expect(COOKIE_WRITE).not.toHaveBeenCalled();
  });

  it.each(REVIEW_FILES)(
    "keeps the actual processing review warning visible for $id",
    async (file) => {
      ROUTING.search = new URLSearchParams({ file: file.id }).toString();
      await mount();
      const disclosure = file.processingDisclosure;
      if (!disclosure) throw new Error("Review warning must explain the limitation");
      const warning = screen.getByText(disclosure, { exact: false });
      expect(warning).toBeVisible();
      expect(warning).toHaveAttribute("role", "status");
      expect(warning).toHaveTextContent("Review required.");
    },
  );

  it("compares the recorded article versions without starting another transform", async () => {
    await mount();
    const file = demoFileFor(null);
    if (!file.comparison) throw new Error("Default recorded result has no comparison");
    const comparison = screen.getByRole("group", { name: "Compare document versions" });
    expect(within(comparison).getByRole("link", { name: "After" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(within(comparison).getByRole("link", { name: "Before" })).toHaveAttribute(
      "href",
      `/demo?file=${encodeURIComponent(file.comparison.beforeId)}`,
    );
    expect(PUBLIC_FETCH).not.toHaveBeenCalled();
    expect(onlyPublicRequests()).toBe(true);
  });

  it("opens local search and routes its chosen result only within the demo", async () => {
    await mount();
    fireEvent.keyDown(window, { ctrlKey: true, key: "k" });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    const target = DEMO_FILES.find((file) => file.id !== demoFileFor(null).id);
    if (!target) throw new Error("Public search needs another fixture");
    const dialog = screen.getByRole("dialog");
    fireEvent.click(
      within(dialog).getByRole("option", { name: (name) => name.includes(target.name) }),
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(ROUTING.push).toHaveBeenCalledExactlyOnceWith(
      `/demo?file=${encodeURIComponent(target.id)}`,
    );
    expect(onlyPublicRequests()).toBe(true);
  });
});
