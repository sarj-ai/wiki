import { fireEvent, render, type RenderResult, screen } from "@testing-library/react";
import { act } from "react";

import type * as DemoDataModule from "./demo-data";
import { DemoPreview } from "./demo-preview";

const LOCAL_FILES = vi.hoisted(() => [
  {
    description: "Local public document",
    downloadFilename: "public-document.docx",
    folder: "documents" as const,
    format: "Word document",
    id: "static-doc",
    keywords: "document",
    mimeType: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    name: "Public document.docx",
    previewFilename: "public-document.html",
  },
  {
    description: "Local public workbook",
    downloadFilename: "public-workbook.xlsx",
    folder: "project" as const,
    format: "Excel spreadsheet",
    id: "static-sheet",
    keywords: "workbook",
    mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    name: "Public workbook.xlsx",
    previewFilename: "public-workbook.html",
  },
]);

vi.mock("./demo-data", async () => {
  const actual = await vi.importActual<typeof DemoDataModule>("./demo-data");
  return { ...actual, DEMO_FILES: LOCAL_FILES };
});

const HTTP = vi.fn<typeof fetch>();
const RELOAD = vi.fn();
const HTML = "<!doctype html><html><body><main>Current public sample</main></body></html>";

describe("owned public preview lifecycle", () => {
  let view: RenderResult | undefined;

  beforeEach(() => {
    view = undefined;
    HTTP.mockReset();
    RELOAD.mockClear();
    HTTP.mockImplementation(async () => {
      await Promise.resolve();
      return new Response(HTML, { headers: { "content-type": "text/html" } });
    });
    vi.useFakeTimers();
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    vi.stubGlobal("fetch", HTTP);
  });

  afterEach(async () => {
    view?.unmount();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  async function showPreview(id = "static-doc", attempt = 0) {
    const file = LOCAL_FILES.find((candidate) => candidate.id === id);
    if (!file) throw new Error("Unknown local test fixture");
    const preview = <DemoPreview file={file} key={`${id}:${attempt}`} onReload={RELOAD} />;
    if (view) view.rerender(preview);
    else view = render(preview);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
  }

  it("never mounts a blank frame while its owned content request is pending", async () => {
    const pending = Promise.withResolvers<Response>();
    HTTP.mockReturnValueOnce(pending.promise);
    await showPreview();
    expect(screen.queryByTitle(/read-only preview/)).toBeNull();
    expect(screen.getByRole("status")).toHaveAccessibleName(/Opening/);
    pending.resolve(new Response(HTML, { headers: { "content-type": "text/html" } }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByTitle(/read-only preview/)).toHaveAttribute("srcdoc", HTML);
    expect(screen.getByTitle(/read-only preview/)).toHaveAttribute(
      "sandbox",
      "allow-popups allow-popups-to-escape-sandbox",
    );
  });

  it("allows citation popups while blocking privileged HTML preview capabilities", async () => {
    await showPreview();
    const permissions = screen
      .getByTitle(/read-only preview/)
      .getAttribute("sandbox")
      ?.split(" ");
    expect(permissions).toEqual(["allow-popups", "allow-popups-to-escape-sandbox"]);
    expect(permissions).not.toContain("allow-scripts");
    expect(permissions).not.toContain("allow-same-origin");
    expect(permissions).not.toContain("allow-forms");
    expect(permissions).not.toContain("allow-top-navigation");
    expect(permissions).not.toContain("allow-top-navigation-by-user-activation");
    expect(permissions).not.toContain("allow-top-navigation-to-custom-protocols");
  });

  it("shows a real missing-file fallback with a download and a working retry", async () => {
    HTTP.mockResolvedValueOnce(new Response("Missing", { status: 404 }));
    await showPreview();
    expect(screen.queryByTitle(/read-only preview/)).toBeNull();
    expect(screen.getByRole("heading", { name: "The preview couldn’t load" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Download sample" })).toHaveAttribute(
      "href",
      "/demo/files/public-document.docx",
    );
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(RELOAD).toHaveBeenCalledOnce();
    await showPreview("static-doc", 1);
    expect(screen.getByTitle(/read-only preview/)).toHaveAttribute("srcdoc", HTML);
    expect(HTTP).toHaveBeenCalledTimes(2);
  });

  it("shows network failure as a fallback rather than a ready empty frame", async () => {
    HTTP.mockRejectedValueOnce(new TypeError("Network failed"));
    await showPreview();
    expect(screen.queryByTitle(/read-only preview/)).toBeNull();
    expect(screen.getByRole("heading", { name: "The preview couldn’t load" })).toBeInTheDocument();
  });

  it("aborts a stalled request at the visible deadline and ignores a late response", async () => {
    const pending = Promise.withResolvers<Response>();
    HTTP.mockReturnValueOnce(pending.promise);
    await showPreview();
    const signal = HTTP.mock.calls[0]?.[1]?.signal;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(signal?.aborted).toBe(true);
    expect(
      screen.getByRole("heading", { name: "The preview is taking a little longer" }),
    ).toBeInTheDocument();
    pending.resolve(new Response(HTML, { headers: { "content-type": "text/html" } }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.queryByTitle(/read-only preview/)).toBeNull();
  });

  it("aborts retired selection ownership and prevents late content replacing the new file", async () => {
    const pending = Promise.withResolvers<Response>();
    HTTP.mockReturnValueOnce(pending.promise);
    await showPreview();
    const retired = HTTP.mock.calls[0]?.[1]?.signal;
    await showPreview("static-sheet");
    expect(retired?.aborted).toBe(true);
    pending.resolve(
      new Response("<html>Retired content</html>", { headers: { "content-type": "text/html" } }),
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByTitle(/read-only preview/)).toHaveAttribute("srcdoc", HTML);
    expect(screen.getByTitle(/read-only preview/)).toHaveAttribute(
      "title",
      "Public workbook.xlsx — read-only preview",
    );
    expect(HTTP).toHaveBeenCalledTimes(2);
  });
});
