import { fireEvent, render, type RenderResult, screen } from "@testing-library/react";
import { act } from "react";

import { demoFileFor, demoHref } from "./demo-data";
import { DemoToolbar } from "./demo-toolbar";

describe("public file actions", () => {
  let view: RenderResult | undefined;

  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  });

  afterEach(() => {
    view?.unmount();
    view = undefined;
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("uses the configured export without exposing a private mutation", () => {
    const file = {
      ...demoFileFor(null),
      downloadUrl: "https://docs.google.com/spreadsheets/d/public-sheet/export?format=xlsx",
    };
    view = render(<DemoToolbar file={file} onReload={vi.fn()} />);
    const download = screen.getByRole("link", { name: `Download ${file.name}` });
    expect(download).toHaveAttribute("href", file.downloadUrl);
    expect(download).toHaveAttribute("target", "_blank");
    expect(download).toHaveAttribute("rel", "noopener noreferrer");
    expect(download).toHaveAttribute("referrerpolicy", "no-referrer");
    expect(screen.queryByRole("form")).toBeNull();
    expect(screen.queryByRole("button", { name: /Polish|Delete|Edit/ })).toBeNull();
  });

  it("omits an unsafe download instead of rendering a private API link", () => {
    const file = { ...demoFileFor(null), downloadUrl: "/api/wiki/files/private-id" };
    view = render(<DemoToolbar file={file} onReload={vi.fn()} />);
    expect(screen.queryByRole("link", { name: `Download ${file.name}` })).toBeNull();
  });

  it("opens the public Drive file without granting the new tab access to the demo", () => {
    const file = {
      ...demoFileFor(null),
      viewUrl: "https://drive.google.com/file/d/public-document/view",
    };
    view = render(<DemoToolbar file={file} onReload={vi.fn()} />);
    const link = screen.getByRole("link", { name: "Open sample in Google Drive" });
    expect(link).toHaveAttribute("href", file.viewUrl);
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(link).toHaveAttribute("referrerpolicy", "no-referrer");
  });

  it("omits an untrusted Open in Drive link", () => {
    const file = { ...demoFileFor(null), viewUrl: "javascript:alert(1)" };
    view = render(<DemoToolbar file={file} onReload={vi.fn()} />);
    expect(screen.queryByRole("link", { name: "Open sample in Google Drive" })).toBeNull();
  });

  it("reloads the current preview", () => {
    const reload = vi.fn();
    view = render(<DemoToolbar file={demoFileFor(null)} onReload={reload} />);
    fireEvent.click(screen.getByRole("button", { name: "Reload preview" }));
    expect(reload).toHaveBeenCalledOnce();
  });

  it("copies a public demo link and clears its success feedback", async () => {
    const file = demoFileFor(null);
    const copy = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue();
    view = render(<DemoToolbar file={file} onReload={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Copy link" }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(copy).toHaveBeenCalledExactlyOnceWith(
      new URL(demoHref(file), window.location.origin).href,
    );
    expect(screen.getByRole("status")).toHaveTextContent("Public demo link copied.");
    expect(screen.getByRole("button", { name: "Link copied" })).toBeInTheDocument();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4_000);
    });
    expect(screen.getByRole("button", { name: "Copy link" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toBeEmptyDOMElement();
  });

  it("shows a useful fallback when clipboard access fails", async () => {
    vi.spyOn(navigator.clipboard, "writeText").mockRejectedValue(new Error("Clipboard denied"));
    view = render(<DemoToolbar file={demoFileFor(null)} onReload={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Copy link" }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByRole("status")).toBeVisible();
    expect(screen.getByRole("status")).toHaveTextContent("Couldn’t copy the link.");
    expect(screen.getByRole("button", { name: "Copy link" })).toBeInTheDocument();
  });
});
