import {
  fireEvent,
  render,
  type RenderResult,
  screen,
} from "@testing-library/react";
import { act, useLayoutEffect, useRef } from "react";
import { hydrateRoot, type Root } from "react-dom/client";
import { renderToString } from "react-dom/server";

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

function PreviewLoadedBeforeEffects() {
  const container = useRef<HTMLDivElement | null>(null);
  useLayoutEffect(() => {
    container.current
      ?.querySelector("iframe")
      ?.dispatchEvent(new Event("load"));
  }, []);
  return (
    <div ref={container}>
      <DemoPreview file={nativeSample()} onReload={RELOAD} />
    </div>
  );
}

describe("native public read-only preview lifecycle", () => {
  let view: RenderResult | undefined;
  let hydratedRoot: Root | undefined;
  let hydrationContainer: HTMLDivElement | undefined;

  beforeEach(() => {
    view = undefined;
    hydratedRoot = undefined;
    hydrationContainer = undefined;
    HTTP.mockReset();
    RELOAD.mockClear();
    vi.useFakeTimers();
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    vi.stubGlobal("fetch", HTTP);
  });

  afterEach(async () => {
    view?.unmount();
    await act(async () => {
      hydratedRoot?.unmount();
      await vi.advanceTimersByTimeAsync(0);
    });
    hydrationContainer?.remove();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  async function showPreview(previewUrl = PREVIEW_URL, attempt = 0) {
    const preview = (
      <DemoPreview
        file={nativeSample(previewUrl)}
        key={attempt}
        onReload={RELOAD}
      />
    );
    if (view) view.rerender(preview);
    else view = render(preview);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
  }

  async function hydratePreview(readyState: DocumentReadyState) {
    const element = <DemoPreview file={nativeSample()} onReload={RELOAD} />;
    const container = document.createElement("div");
    hydrationContainer = container;
    const markup = new DOMParser().parseFromString(
      renderToString(element),
      "text/html",
    );
    container.append(...markup.body.childNodes);
    document.body.append(container);
    const frame = container.querySelector("iframe");
    if (!(frame instanceof HTMLIFrameElement)) {
      throw new TypeError("The SSR fixture must contain its native iframe");
    }
    frame.dispatchEvent(new Event("load"));
    const sourceSetter = vi.spyOn(frame, "src", "set");
    const documentState = vi
      .spyOn(document, "readyState", "get")
      .mockReturnValue(readyState);
    await act(async () => {
      hydratedRoot = hydrateRoot(container, element);
      await vi.advanceTimersByTimeAsync(0);
    });
    return { frame, sourceSetter, documentState };
  }

  it("recovers a native iframe loaded before hydration without another navigation", async () => {
    const { frame, sourceSetter } = await hydratePreview("complete");
    expect(screen.getByTitle(/read-only preview/)).toBe(frame);
    expect(frame).toHaveAttribute("src", PREVIEW_URL);
    expect(frame).toHaveAttribute("aria-hidden", "false");
    expect(screen.getByRole("status")).toHaveTextContent("Preview opened.");
    expect(sourceSetter).not.toHaveBeenCalled();
    expect(document.querySelectorAll("iframe")).toHaveLength(1);
    expect(HTTP).not.toHaveBeenCalled();
  });

  it("waits for the initial parent load when hydration precedes document completion", async () => {
    const { frame, sourceSetter, documentState } =
      await hydratePreview("interactive");
    expect(frame).toHaveAttribute("aria-hidden", "true");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    screen.getByRole("button", { name: "Try again" }).focus();
    documentState.mockReturnValue("complete");
    fireEvent.load(window);
    expect(screen.getByTitle(/read-only preview/)).toBe(frame);
    expect(frame).toHaveAttribute("aria-hidden", "false");
    expect(frame).toHaveFocus();
    expect(screen.queryByRole("heading")).toBeNull();
    expect(sourceSetter).not.toHaveBeenCalled();
    expect(HTTP).not.toHaveBeenCalled();
  });

  it("keeps a preview ready when its load precedes the passive deadline setup", async () => {
    view = render(<PreviewLoadedBeforeEffects />);
    const frame = screen.getByTitle(/read-only preview/);
    expect(frame).toHaveAttribute("aria-hidden", "false");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(frame).toHaveAttribute("aria-hidden", "false");
    expect(screen.queryByRole("heading")).toBeNull();
    expect(screen.getByRole("status")).toHaveTextContent("Preview opened.");
    expect(HTTP).not.toHaveBeenCalled();
  });

  it("embeds the public preview directly without fetching private APIs or manufactured HTML", async () => {
    await showPreview();
    const frame = screen.getByTitle(/read-only preview/);
    expect(frame).toHaveAttribute("src", PREVIEW_URL);
    expect(frame).not.toHaveAttribute("srcdoc");
    expect(frame).toHaveAttribute("sandbox", "allow-scripts allow-same-origin");
    expect(frame.getAttribute("sandbox")?.split(" ")).not.toContain(
      "allow-popups",
    );
    expect(frame.getAttribute("sandbox")?.split(" ")).not.toContain(
      "allow-popups-to-escape-sandbox",
    );
    expect(frame).toHaveAttribute("referrerpolicy", "no-referrer");
    expect(frame).toHaveAttribute("aria-hidden", "true");
    expect(HTTP).not.toHaveBeenCalled();
    fireEvent.load(frame);
    expect(frame).toHaveAttribute("aria-hidden", "false");
    expect(screen.getByRole("status")).toHaveTextContent("Preview opened.");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(screen.queryByRole("heading")).toBeNull();
    expect(frame).toBeInTheDocument();
  });

  it("offers the actual export and retry when a preview route is rejected", async () => {
    await showPreview("https://docs.google.com/document/d/public-doc/edit");
    expect(screen.queryByTitle(/read-only preview/)).toBeNull();
    expect(
      screen.getByRole("heading", { name: "The preview couldn’t load" }),
    ).toBeInTheDocument();
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

  it("announces a slow preview while allowing the same frame to finish loading", async () => {
    await showPreview();
    const frame = screen.getByTitle(/read-only preview/);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(screen.getByTitle(/read-only preview/)).toBe(frame);
    expect(
      screen.getByRole("heading", {
        name: "The preview is taking a little longer",
      }),
    ).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(
      "The preview is still loading.",
    );
    expect(frame).toHaveAttribute("aria-hidden", "true");
    expect(frame).toHaveAttribute("tabindex", "-1");
    screen.getByRole("button", { name: "Try again" }).focus();
    fireEvent.load(frame);
    expect(screen.getByTitle(/read-only preview/)).toBe(frame);
    expect(frame).toHaveAttribute("aria-hidden", "false");
    expect(frame).toHaveFocus();
    expect(screen.queryByRole("heading")).toBeNull();
    expect(screen.getByRole("status")).toHaveTextContent("Preview opened.");
    expect(HTTP).not.toHaveBeenCalled();
  });

  it("clears the retired frame deadline before mounting a fresh attempt", async () => {
    vi.spyOn(document, "readyState", "get").mockReturnValue("complete");
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
    expect(screen.getByTitle(/read-only preview/)).toHaveAttribute(
      "aria-hidden",
      "true",
    );
    fireEvent.load(window);
    expect(screen.getByTitle(/read-only preview/)).toHaveAttribute(
      "aria-hidden",
      "true",
    );
    fireEvent.load(screen.getByTitle(/read-only preview/));
    expect(HTTP).not.toHaveBeenCalled();
  });

  it("retires the previous source when a fixture's preview URL changes", async () => {
    await showPreview();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(9_000);
    });
    const nextUrl =
      "https://docs.google.com/document/d/next-public-doc/preview";
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
  ])(
    "fails closed for an untrusted preview without a local fallback: %s",
    async (previewUrl) => {
      await showPreview(previewUrl);
      expect(screen.queryByTitle(/read-only preview/)).toBeNull();
      expect(
        screen.getByRole("heading", { name: "The preview couldn’t load" }),
      ).toBeInTheDocument();
      expect(HTTP).not.toHaveBeenCalled();
    },
  );
});
