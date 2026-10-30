import { fireEvent, render, type RenderResult, screen } from "@testing-library/react";
import { act } from "react";

import { type DemoFile, demoFileFor } from "./demo-data";
import { DemoPreview } from "./demo-preview";

const HTTP = vi.fn<typeof fetch>();
const RELOAD = vi.fn();
const PREVIEW_URL =
  "https://docs.google.com/document/d/11ZKT_SKqzN17KzjRQ_iyoqgM6HjYEdFvokNOQFpExlk/preview";
const DOWNLOAD_URL =
  "https://docs.google.com/document/d/11ZKT_SKqzN17KzjRQ_iyoqgM6HjYEdFvokNOQFpExlk/export?format=docx";

function nativeSample(previewUrl = PREVIEW_URL): DemoFile {
  return {
    ...demoFileFor(null),
    downloadFilename: "public-native.docx",
    downloadUrl: DOWNLOAD_URL,
    id: "native-preview-fixture",
    name: "Public native document",
    previewUrl,
  };
}

describe("native public read-only preview lifecycle", () => {
  let view: RenderResult | undefined;

  beforeEach(() => {
    view = undefined;
    HTTP.mockReset();
    RELOAD.mockClear();
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

  async function showPreview(previewUrl = PREVIEW_URL, attempt = 0) {
    const preview = <DemoPreview file={nativeSample(previewUrl)} key={attempt} onReload={RELOAD} />;
    if (view) view.rerender(preview);
    else view = render(preview);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
  }

  it("embeds the public preview directly without fetching private APIs or manufactured HTML", async () => {
    await showPreview();
    const frame = screen.getByTitle(/read-only preview/);
    expect(frame).toHaveAttribute("src", PREVIEW_URL);
    expect(frame).not.toHaveAttribute("srcdoc");
    expect(frame).toHaveAttribute("sandbox", "allow-scripts allow-same-origin");
    expect(frame.getAttribute("sandbox")?.split(" ")).not.toContain("allow-popups");
    expect(frame.getAttribute("sandbox")?.split(" ")).not.toContain(
      "allow-popups-to-escape-sandbox",
    );
    expect(frame).toHaveAttribute("referrerpolicy", "no-referrer");
    expect(frame).toHaveAttribute("aria-hidden", "true");
    expect(HTTP).not.toHaveBeenCalled();
    fireEvent.load(frame);
    expect(frame).toHaveAttribute("aria-hidden", "false");
    expect(screen.queryByRole("status")).toBeNull();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(screen.queryByRole("heading")).toBeNull();
    expect(frame).toBeInTheDocument();
  });

  it("offers the actual export and retry when a preview route is rejected", async () => {
    await showPreview("https://docs.google.com/document/d/public-doc/edit");
    expect(screen.queryByTitle(/read-only preview/)).toBeNull();
    expect(screen.getByRole("heading", { name: "The preview couldn’t load" })).toBeInTheDocument();
    const download = screen.getByRole("link", { name: "Download sample" });
    expect(download).toHaveAttribute("href", DOWNLOAD_URL);
    expect(download).toHaveAttribute("download", "public-native.docx");
    expect(download).toHaveAttribute("rel", "noopener noreferrer");
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(RELOAD).toHaveBeenCalledOnce();
    expect(HTTP).not.toHaveBeenCalled();
    await showPreview(PREVIEW_URL, 1);
    fireEvent.load(screen.getByTitle(/read-only preview/));
    expect(screen.queryByRole("heading")).toBeNull();
  });

  it("removes a stalled native frame at the visible deadline and ignores its late load", async () => {
    await showPreview();
    const retired = screen.getByTitle(/read-only preview/);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(screen.queryByTitle(/read-only preview/)).toBeNull();
    expect(
      screen.getByRole("heading", { name: "The preview is taking a little longer" }),
    ).toBeInTheDocument();
    fireEvent.load(retired);
    expect(screen.queryByTitle(/read-only preview/)).toBeNull();
    expect(HTTP).not.toHaveBeenCalled();
  });

  it("clears the retired frame deadline before mounting a fresh attempt", async () => {
    await showPreview();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(9_000);
    });
    await showPreview(PREVIEW_URL, 1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_500);
    });
    expect(screen.getByTitle(/read-only preview/)).toBeInTheDocument();
    expect(screen.queryByRole("heading")).toBeNull();
    fireEvent.load(screen.getByTitle(/read-only preview/));
    expect(HTTP).not.toHaveBeenCalled();
  });

  it("retires the previous source when a fixture's preview URL changes", async () => {
    await showPreview();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(9_000);
    });
    const nextUrl = "https://docs.google.com/document/d/next-public-doc/preview";
    await showPreview(nextUrl);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_500);
    });
    const frame = screen.getByTitle(/read-only preview/);
    expect(frame).toHaveAttribute("src", nextUrl);
    expect(frame).toHaveAttribute("aria-hidden", "true");
    expect(screen.queryByRole("heading")).toBeNull();
    fireEvent.load(frame);
    expect(frame).toHaveAttribute("aria-hidden", "false");
    expect(HTTP).not.toHaveBeenCalled();
  });

  it.each([
    "https://docs.google.com/document/d/public-doc/edit",
    "https://docs.google.com/document/d/public-doc/preview?edit=true",
    "https://evil.example/document/d/public-doc/preview",
    "/api/wiki/files/private-id",
  ])("fails closed for an untrusted preview without a local fallback: %s", async (previewUrl) => {
    await showPreview(previewUrl);
    expect(screen.queryByTitle(/read-only preview/)).toBeNull();
    expect(screen.getByRole("heading", { name: "The preview couldn’t load" })).toBeInTheDocument();
    expect(HTTP).not.toHaveBeenCalled();
  });
});
