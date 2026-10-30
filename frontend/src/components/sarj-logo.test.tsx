// @vitest-environment happy-dom

import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import { describe, expect, it } from "vitest";

import { SarjLogo } from "@/components/sarj-logo";

describe("SarjLogo", () => {
  it("renders one fixed-size theme-aware inline image", () => {
    const container = document.createElement("div");
    const root = createRoot(container);
    flushSync(() => {
      root.render(<SarjLogo size={28} />);
    });

    const logo = container.querySelector('[role="img"][aria-label="Sarj"]');
    expect(container.querySelectorAll("svg")).toHaveLength(1);
    expect(container.querySelectorAll("img")).toHaveLength(0);
    expect(logo).toHaveAttribute("height", "28");
    expect(logo).toHaveAttribute("width", "52");
    expect(logo).toHaveAttribute("fill", "currentColor");
    expect(logo).toHaveClass("block", "shrink-0");

    flushSync(() => {
      root.unmount();
    });
  });
});
