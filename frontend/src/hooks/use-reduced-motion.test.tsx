import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import { describe, expect, it, vi } from "vitest";

import { useReducedMotion } from "@/hooks/use-reduced-motion";

function Preference() {
  return <span>{String(useReducedMotion())}</span>;
}

describe("shared reduced-motion preference", () => {
  it("updates multiple consumers using one native listener and releases it", () => {
    let reduced = false;
    let changed: (() => void) | undefined;
    const addEventListener = vi.fn((_event: string, listener: () => void) => {
      changed = listener;
    });
    const removeEventListener = vi.fn();
    vi.stubGlobal("matchMedia", () => ({
      addEventListener,
      get matches() {
        return reduced;
      },
      removeEventListener,
    }));
    const container = document.createElement("div");
    const root = createRoot(container);
    try {
      flushSync(() =>
        root.render(
          <>
            <Preference />
            <Preference />
          </>,
        ),
      );
      expect(addEventListener).toHaveBeenCalledOnce();
      expect(container.textContent).toBe("falsefalse");
      reduced = true;
      flushSync(() => changed?.());
      expect(container.textContent).toBe("truetrue");
    } finally {
      flushSync(() => root.unmount());
      vi.unstubAllGlobals();
    }
    expect(removeEventListener).toHaveBeenCalledOnce();
  });
});
