import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import { renderToString } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { SarjLoader } from "@/components/sarj-loader";

describe("Sarj loading status", () => {
  it("announces one status and lets the browser choose the static motion-safe wordmark", () => {
    const container = document.createElement("div");
    const root = createRoot(container);
    try {
      flushSync(() => root.render(<SarjLoader label="Loading workspace…" />));
      expect(container.querySelectorAll('[role="status"]')).toHaveLength(1);
      expect(container.querySelector('[role="status"]')).toHaveAttribute(
        "aria-label",
        "Loading workspace…",
      );
      expect(container.querySelector("picture source")).toHaveAttribute(
        "media",
        "(prefers-reduced-motion: reduce)",
      );
      expect(container.querySelector("source")).toHaveAttribute(
        "srcset",
        "/brand/sarj-loader-still-12132f41.svg",
      );
      expect(container.querySelector("img")).toHaveAttribute(
        "src",
        "/brand/sarj-loader-12132f41.svg",
      );
      expect(container.querySelector("img")).toHaveAttribute("alt", "");
      expect(container.querySelector("canvas")).toBeNull();
    } finally {
      flushSync(() => root.unmount());
    }
  });

  it("renders the complete motion selection and loading label on the server", () => {
    const view = renderToString(<SarjLoader label="Loading document…" />);
    expect(view).toContain("(prefers-reduced-motion: reduce)");
    expect(view).toContain("Loading document…");
    expect(view).not.toContain("<script");
    const documentFragment = new DOMParser().parseFromString(view, "text/html");
    expect(
      documentFragment.querySelector(
        'link[rel="preload"][href="/brand/sarj-loader-12132f41.svg"]:not([media])',
      ),
    ).toBeNull();
  });
});
